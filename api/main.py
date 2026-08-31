"""
iTest-Agent FastAPI 应用入口

提供 REST 接口：
- POST /api/v1/pipeline                上传 PRD 并触发全流程（异步/同步）
- GET  /api/v1/tasks/{task_id}         查询任务状态与阶段产物
- GET  /api/v1/tasks/{task_id}/report  下载测试报告（md / pdf / json）
- POST /api/v1/tasks/{task_id}/incremental  提交变更功能 ID 触发增量更新
- GET  /health                         健康检查

启动：
    uvicorn api.main:app --host 0.0.0.0 --port 8000
或：
    python -m api.main
"""

import json
import os
import shutil
import sys
import threading
import traceback
from typing import Dict, Optional

# 添加项目根目录到 Python 路径
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from api.schemas import (
    IncrementalRequest,
    PipelineOptions,
    PipelineResponse,
    TaskStatusResponse,
)
from api.task_store import TaskStore


# =============================================================================
# 应用初始化
# =============================================================================

OUTPUT_ROOT = os.getenv("ITEST_OUTPUT_ROOT", os.path.join(_PROJECT_ROOT, "output"))
TASK_STORE = TaskStore(OUTPUT_ROOT)

app = FastAPI(
    title="iTest-Agent API",
    description="基于 LangGraph 多 Agent 协作的智能测试用例自动生成与执行系统",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# =============================================================================
# 流水线执行（后台线程）
# =============================================================================


def _run_pipeline(task_id: str) -> None:
    """在后台线程执行完整工作流并更新任务状态"""
    try:
        task = TASK_STORE.get_task(task_id)
        if task is None:
            return
        TASK_STORE.update_task(task_id, status="running", phase="init")

        options = task.get("options", {}) or {}
        prd_path = task["prd_path"]
        output_dir = task["output_dir"]
        os.makedirs(output_dir, exist_ok=True)

        # LLM 配置走任务级 state（不再污染进程环境变量，支持并发任务）

        from graph.state import (
            create_initial_state,
            create_incremental_state,
        )
        from graph.workflow import build_itest_workflow

        checkpoint_db = os.path.join(output_dir, "itest_checkpoints.db")
        # 每个任务使用独立的知识库目录（只读副本），避免并发任务竞争共享 chroma_db
        shared_kb = os.getenv(
            "ITEST_KB_DIR", os.path.join(_PROJECT_ROOT, "chroma_db")
        )
        kb_dir = os.path.join(output_dir, "kb")
        if os.path.isdir(shared_kb) and not os.path.exists(kb_dir):
            try:
                import shutil

                shutil.copytree(shared_kb, kb_dir)
            except Exception:
                kb_dir = shared_kb  # 复制失败则退回共享目录
        else:
            kb_dir = shared_kb
        common = dict(
            llm_model=options.get("model", "gpt-4o-mini"),
            llm_api_key=options.get("llm_api_key", "") or "",
            llm_base_url=options.get("llm_base_url", "") or "",
            mock_llm=bool(options.get("mock_llm", False)),
            output_dir=output_dir,
            kb_persist_dir=kb_dir,
            checkpoint_db_path=checkpoint_db,
            config={
                "execution_mode": options.get("execution_mode", "simulated"),
                "max_review_rounds": options.get("max_review_rounds", 3),
            },
        )

        if task.get("incremental"):
            base_task_id = task.get("base_task_id", "")
            base_task = TASK_STORE.get_task(base_task_id) if base_task_id else None
            prev_analysis = ""
            if base_task:
                prev_analysis = os.path.join(
                    base_task["output_dir"],
                    f"{os.path.splitext(base_task['prd_filename'])[0]}_analysis_result.json",
                )
            changed = (options.get("changed_function_ids") or [])
            TASK_STORE.append_message(
                task_id,
                f"增量更新模式 — 变更功能: {changed}，"
                f"前次分析: {prev_analysis or '未找到'}",
            )
            initial_state = create_incremental_state(
                prd_path=prd_path,
                previous_analysis_path=prev_analysis,
                changed_function_ids=changed,
                change_type=options.get("change_type", "incremental"),
                **common,
            )
        else:
            initial_state = create_initial_state(
                prd_path=prd_path,
                **common,
            )

        workflow = build_itest_workflow(checkpoint_db_path=checkpoint_db)
        config = {"configurable": {"thread_id": task_id}}
        # 流式执行：逐节点更新任务 phase（供 UI 进度与阶段耗时统计）
        merged: Dict = {}
        for event in workflow.app.stream(initial_state, config=config):
            # event 形如 {node_name: state_delta}
            for _node, delta in event.items():
                if not isinstance(delta, dict):
                    continue
                merged.update(delta)
                _phase = delta.get("phase", "")
                if _phase:
                    TASK_STORE.update_task(task_id, phase=_phase)

        # ── 汇总产物到任务记录 ──
        analysis = merged.get("analysis_result", {}) or {}
        suite = merged.get("test_suite", {}) or {}
        review = merged.get("review_result", {}) or {}
        execution = merged.get("execution_result", {}) or {}

        # 生成富报告（Markdown + PDF）与 Excel 导出
        from agents.report_generator import ReportGenerator

        exec_log = os.path.join(output_dir, "execution_log.json")
        suite_path = os.path.join(output_dir, "test_suite.json")
        analysis_path = analysis.get("file_path", "")
        review_path = os.path.join(output_dir, "review_result.json")

        report_path = merged.get("report_path", "")
        try:
            generator = ReportGenerator(pdf_enabled=True)
            report_result = generator.generate(
                execution_log_path=exec_log,
                test_suite_path=suite_path,
                analysis_result_path=analysis_path,
                review_result_path=review_path,
                output_dir=output_dir,
            )
            report_path = report_result.get("markdown") or report_path
        except Exception as e:
            TASK_STORE.append_message(task_id, f"富报告生成失败（保留基础报告）: {e}")

        try:
            from exporters.excel_exporter import ExcelExporter

            with open(suite_path, "r", encoding="utf-8") as f:
                suite_data = f.read()
            from models.test_case import TestSuite

            suite_obj = TestSuite.model_validate_json(suite_data)
            ExcelExporter().export_suite(
                suite_obj, os.path.join(output_dir, "test_suite.xlsx")
            )
        except Exception as e:
            TASK_STORE.append_message(task_id, f"Excel 导出失败: {e}")

        traceability_path = suite.get("traceability_matrix_path", "")
        status = (
            "completed"
            if merged.get("phase") == "completed"
            else "failed"
        )

        # ── 对最终报告追加 LLM-as-a-Judge 评估（前端报告 Tab 展示）──
        try:
            from execution.llm_judge import judge_report, report_to_markdown

            judge_result = judge_report(report_path, use_env_key=True)
            with open(report_path, "a", encoding="utf-8") as f:
                f.write("\n" + report_to_markdown(judge_result))
            TASK_STORE.append_message(
                task_id,
                f"报告质量评估完成: {judge_result['total_score']}/5.0 "
                f"(评级 {judge_result['rating']}, 模式 {judge_result['judge_mode']})",
            )
        except Exception as e:
            # Judge 失败不应阻断主流程
            TASK_STORE.append_message(task_id, f"报告质量评估失败（已跳过）: {e}")

        raw_messages = merged.get("messages", []) or []
        messages = [
            m.content if hasattr(m, "content") else str(m)
            for m in raw_messages
        ]
        TASK_STORE.update_task(
            task_id,
            status=status,
            phase=merged.get("phase", ""),
            analysis=analysis,
            test_suite=suite,
            review=review,
            execution=execution,
            report_path=report_path,
            traceability_path=traceability_path,
            messages=messages,
            error="",
        )

    except Exception as e:
        traceback.print_exc()
        TASK_STORE.update_task(
            task_id,
            status="failed",
            error=f"{type(e).__name__}: {e}",
        )


def _spawn_task(
    task_id: str,
    sync: bool,
    timeout: float = 600.0,
) -> Dict:
    """启动后台任务；sync=true 时同步等待"""
    thread = threading.Thread(
        target=_run_pipeline,
        args=(task_id,),
        daemon=True,
        name=f"itest-task-{task_id}",
    )
    thread.start()
    if sync:
        task = TaskStore.wait_for_completion(TASK_STORE, task_id, timeout=timeout)
        return _to_status_response(task)
    return TASK_STORE.get_task(task_id)


def _to_status_response(task: Dict) -> TaskStatusResponse:
    """将任务字典转换为响应模型"""
    messages = task.get("messages", []) or []
    messages = [
        m.content if hasattr(m, "content") else str(m)
        for m in messages
    ]
    analysis = dict(task.get("analysis", {}) or {})
    test_suite = dict(task.get("test_suite", {}) or {})

    # 补充明细字段：任务详情只存汇总，function_tree / test_cases 需从产物文件读取。
    # 这样前端（React）无需自己读服务器文件即可渲染完整结果。
    try:
        _ap = analysis.get("file_path", "")
        if _ap and os.path.exists(_ap):
            with open(_ap, "r", encoding="utf-8") as f:
                _adata = json.load(f)
            if "function_tree" in _adata and "function_tree" not in analysis:
                analysis["function_tree"] = _adata["function_tree"]
            if "summary" in _adata and "summary" not in analysis:
                analysis["summary"] = _adata["summary"]
    except Exception:
        pass

    try:
        _sp = test_suite.get("file_path", "")
        if _sp and os.path.exists(_sp):
            with open(_sp, "r", encoding="utf-8") as f:
                _sdata = json.load(f)
            if "test_cases" in _sdata and "test_cases" not in test_suite:
                test_suite["test_cases"] = _sdata["test_cases"]
    except Exception:
        pass

    return TaskStatusResponse(
        task_id=task.get("task_id", ""),
        status=task.get("status", ""),
        phase=task.get("phase", ""),
        created_at=task.get("created_at", ""),
        updated_at=task.get("updated_at", ""),
        prd_filename=task.get("prd_filename", ""),
        incremental=bool(task.get("incremental", False)),
        analysis=analysis,
        test_suite=test_suite,
        review=task.get("review", {}) or {},
        execution=task.get("execution", {}) or {},
        report_path=task.get("report_path", ""),
        traceability_path=task.get("traceability_path", ""),
        phase_times=task.get("phase_times", {}) or {},
        messages=messages,
        error=task.get("error", ""),
    )


# =============================================================================
# 接口
# =============================================================================


@app.get("/health")
def health() -> dict:
    """健康检查"""
    return {"status": "ok", "service": "itest-agent", "version": "1.0.0"}


@app.post("/api/v1/pipeline", response_model=PipelineResponse)
async def run_pipeline(
    file: UploadFile = File(..., description="PRD Markdown 文件"),
    model: str = Form("gpt-4o-mini"),
    execution_mode: str = Form("simulated"),
    mock_llm: bool = Form(False),
    max_review_rounds: int = Form(3),
    sync: bool = Form(False),
    target_url: str = Form(""),
    api_base_url: str = Form(""),
    llm_api_key: str = Form(""),
    llm_base_url: str = Form(""),
):
    """
    上传 PRD 并触发完整流水线：
    需求分析 → 用例生成 → 用例评审 → 用例执行 → 报告生成
    """
    filename = file.filename or "prd.md"
    if not filename.lower().endswith(".md"):
        raise HTTPException(
            status_code=400,
            detail="仅支持 Markdown 格式的 PRD（.md）",
        )

    task_id = TASK_STORE.create_task(
        prd_filename=filename,
        prd_path="",  # 占位，下面写入实际路径
        output_dir=os.path.join(OUTPUT_ROOT, "placeholder"),
        options={
            "model": model,
            "execution_mode": execution_mode,
            "mock_llm": mock_llm,
            "max_review_rounds": max_review_rounds,
            "target_url": target_url,
            "api_base_url": api_base_url,
            "llm_api_key": llm_api_key,
            "llm_base_url": llm_base_url,
        },
    )

    # 写入 PRD 文件
    output_dir = os.path.join(OUTPUT_ROOT, task_id, "prd")
    os.makedirs(output_dir, exist_ok=True)
    prd_path = os.path.join(output_dir, filename)
    content = await file.read()
    with open(prd_path, "wb") as f:
        f.write(content)

    TASK_STORE.update_task(
        task_id,
        prd_path=prd_path,
        output_dir=os.path.join(OUTPUT_ROOT, task_id, "output"),
    )
    TASK_STORE.append_message(task_id, f"PRD 已上传: {filename}")

    _spawn_task(task_id, sync=sync)
    task = TASK_STORE.get_task(task_id)
    return PipelineResponse(
        task_id=task_id,
        status=task.get("status", "pending"),
        detail=(
            "任务已提交，使用 GET /api/v1/tasks/{task_id} 查询进度"
            if not sync
            else "任务已同步执行完成"
        ),
    )


@app.get("/api/v1/tasks/{task_id}", response_model=TaskStatusResponse)
def get_task(task_id: str):
    """查询任务状态与阶段产物"""
    task = TASK_STORE.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"任务不存在: {task_id}")
    return _to_status_response(task)


@app.get("/api/v1/tasks")
def list_tasks(limit: int = Query(20, ge=1, le=100)):
    """列出最近任务"""
    tasks = TASK_STORE.list_tasks(limit=limit)
    return {"total": len(tasks), "tasks": [_to_status_response(t) for t in tasks]}


@app.get("/api/v1/tasks/{task_id}/report")
def download_report(
    task_id: str,
    format: str = Query("md", pattern="^(md|pdf|json)$"),
):
    """下载测试报告（markdown / pdf / json）"""
    task = TASK_STORE.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"任务不存在: {task_id}")
    output_dir = task.get("output_dir", "")

    if format == "json":
        exec_log = os.path.join(output_dir, "execution_log.json")
        if not os.path.exists(exec_log):
            raise HTTPException(status_code=404, detail="执行日志不存在")
        return FileResponse(exec_log, filename=f"{task_id}_execution_log.json")

    if format == "pdf":
        pdf_path = os.path.join(output_dir, "test_report.pdf")
        if not os.path.exists(pdf_path):
            raise HTTPException(status_code=404, detail="PDF 报告不存在（可检查服务日志）")
        return FileResponse(pdf_path, filename=f"{task_id}_test_report.pdf")

    md_path = os.path.join(output_dir, "test_report.md")
    if not os.path.exists(md_path):
        raise HTTPException(status_code=404, detail="Markdown 报告不存在")
    return FileResponse(md_path, filename=f"{task_id}_test_report.md")


@app.post("/api/v1/tasks/{task_id}/incremental", response_model=PipelineResponse)
async def incremental_update(
    task_id: str,
    request: IncrementalRequest,
    sync: bool = Query(False, description="是否同步等待执行完成"),
):
    """
    增量更新：仅对变更的功能 ID 重新生成用例，
    其余功能从历史分析结果继承。
    """
    base_task = TASK_STORE.get_task(task_id)
    if base_task is None:
        raise HTTPException(status_code=404, detail=f"任务不存在: {task_id}")
    if base_task.get("status") != "completed":
        raise HTTPException(
            status_code=400,
            detail="基础任务未完成，无法进行增量更新",
        )

    new_task_id = TASK_STORE.create_task(
        prd_filename=base_task.get("prd_filename", "prd.md"),
        prd_path=base_task.get("prd_path", ""),
        output_dir=os.path.join(OUTPUT_ROOT, "placeholder"),
        options={
            **base_task.get("options", {}),
            "changed_function_ids": request.changed_function_ids,
            "change_type": request.change_type,
        },
        incremental=True,
        base_task_id=task_id,
    )

    TASK_STORE.update_task(
        new_task_id,
        output_dir=os.path.join(OUTPUT_ROOT, new_task_id, "output"),
    )
    TASK_STORE.append_message(
        new_task_id,
        f"增量更新提交 — 变更功能: {request.changed_function_ids}",
    )

    _spawn_task(new_task_id, sync=sync)
    task = TASK_STORE.get_task(new_task_id)
    return PipelineResponse(
        task_id=new_task_id,
        status=task.get("status", "pending"),
        detail="增量更新任务已提交",
    )


@app.delete("/api/v1/tasks/{task_id}")
def delete_task(task_id: str):
    """删除任务产物（仅清理输出目录与任务记录）"""
    task = TASK_STORE.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"任务不存在: {task_id}")
    output_dir = os.path.join(OUTPUT_ROOT, task_id)
    if os.path.isdir(output_dir):
        shutil.rmtree(output_dir)
    # 从内存任务表移除（任务列表文件会持久化空记录，这里直接移除）
    TASK_STORE._tasks.pop(task_id, None)
    TASK_STORE._save()
    return JSONResponse({"deleted": task_id})


def main() -> None:
    """python -m api.main"""
    import uvicorn

    port = int(os.getenv("ITEST_API_PORT", "8000"))
    uvicorn.run("api.main:app", host="0.0.0.0", port=port, reload=False)


if __name__ == "__main__":
    main()

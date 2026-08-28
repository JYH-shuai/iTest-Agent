"""
iTest-Agent Gradio Web UI

对接 FastAPI 后端（默认 http://127.0.0.1:8000）：
- 上传 PRD 触发全流程（支持 Mock LLM 离线演示）
- 实时轮询任务进度（5 阶段）
- 查看需求分析 / 用例 / 评审 / 执行 / 报告
- 增量更新 / 历史任务

启动：
    python frontend/app.py
依赖：gradio, httpx
"""

import json
import os
import tempfile
import time

import gradio as gr
import httpx

API_BASE = os.getenv("ITEST_API_BASE", "http://127.0.0.1:8000")

PHASES = ["analyzing", "generating", "reviewing", "executing", "reporting"]
PHASE_LABELS = {
    "analyzing": "① 需求分析",
    "generating": "② 用例生成",
    "reviewing": "③ 用例评审",
    "executing": "④ 用例执行",
    "reporting": "⑤ 报告生成",
    "init": "初始化",
    "completed": "✅ 完成",
}

STATUS_LABELS = {
    "pending": "⏳ 排队中",
    "running": "🔄 运行中",
    "completed": "✅ 完成",
    "failed": "❌ 失败",
}


# ─────────────────────────────────────────────
# API 封装
# ─────────────────────────────────────────────

def api_health() -> dict:
    try:
        r = httpx.get(f"{API_BASE}/health", timeout=5)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        return {"status": "offline", "error": str(e)}


def api_start_pipeline(prd_file, prd_text, model, execution_mode,
                       mock_llm, max_review_rounds, llm_api_key="",
                       llm_base_url="", target_url="", sync=False) -> dict:
    """上传 PRD 并触发流水线，返回 {task_id, status, detail}"""
    # 文件优先，其次粘贴文本
    data = {
        "model": model,
        "execution_mode": execution_mode,
        "mock_llm": "true" if mock_llm else "false",
        "max_review_rounds": str(max_review_rounds),
        "sync": "true" if sync else "false",
        "llm_api_key": llm_api_key or "",
        "llm_base_url": llm_base_url or "",
        "target_url": target_url or "",
    }
    if prd_file is not None:
        path = prd_file if isinstance(prd_file, str) else prd_file.name
        with open(path, "rb") as f:
            files = {"file": (os.path.basename(path), f, "text/markdown")}
            r = httpx.post(f"{API_BASE}/api/v1/pipeline",
                           files=files, data=data, timeout=30)
    elif prd_text and prd_text.strip():
        files = {"file": ("pasted_prd.md", prd_text.encode("utf-8"),
                          "text/markdown")}
        r = httpx.post(f"{API_BASE}/api/v1/pipeline",
                       files=files, data=data, timeout=30)
    else:
        raise ValueError("请上传 PRD 文件或粘贴 PRD 文本（二选一）")
    r.raise_for_status()
    return r.json()


def api_get_task(task_id: str) -> dict:
    r = httpx.get(f"{API_BASE}/api/v1/tasks/{task_id}", timeout=15)
    r.raise_for_status()
    return r.json()


def api_list_tasks(limit=20) -> list:
    r = httpx.get(f"{API_BASE}/api/v1/tasks", params={"limit": limit},
                  timeout=15)
    r.raise_for_status()
    return r.json().get("tasks", [])


def api_incremental(task_id: str, function_ids: list) -> dict:
    r = httpx.post(
        f"{API_BASE}/api/v1/tasks/{task_id}/incremental",
        json={"changed_function_ids": function_ids,
              "change_type": "incremental"},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


def api_report_md(task_id: str) -> str:
    r = httpx.get(f"{API_BASE}/api/v1/tasks/{task_id}/report",
                  params={"format": "md"}, timeout=15)
    r.raise_for_status()
    return r.text


def api_report_file(task_id: str, fmt: str) -> str:
    """下载报告到临时文件，返回路径（供 gr.File 展示）"""
    r = httpx.get(f"{API_BASE}/api/v1/tasks/{task_id}/report",
                  params={"format": fmt}, timeout=30)
    r.raise_for_status()
    suffix = {"md": ".md", "pdf": ".pdf", "json": ".json"}[fmt]
    tmp = tempfile.NamedTemporaryFile(
        delete=False, suffix=f"_{task_id[:8]}{suffix}",
        prefix="itest_report_")
    tmp.write(r.content)
    tmp.close()
    return tmp.name


# ─────────────────────────────────────────────
# 展示辅助
# ─────────────────────────────────────────────

def phase_progress(task: dict) -> str:
    """根据 phase 生成进度条文本（含各阶段耗时）"""
    phase = task.get("phase", "")
    status = task.get("status", "")
    # 阶段耗时文本
    times = task.get("phase_times", {}) or {}
    times = {k: v for k, v in times.items() if not k.startswith("_")}
    time_parts = [
        f"{PHASE_LABELS.get(k, k)} {v}s" for k, v in times.items()
        if v is not None and v != ""
    ]
    time_text = f"\n\n⏱ {' · '.join(time_parts)}" if time_parts else ""
    if status == "completed":
        return "✅✅✅✅✅ 全部完成" + time_text
    if status == "failed":
        return "❌ 任务失败" + time_text
    try:
        idx = PHASES.index(phase)
    except ValueError:
        idx = 0
    done = "✅" * idx + "🔄" + "⬜" * (len(PHASES) - idx - 1)
    return f"{done} 当前：{PHASE_LABELS.get(phase, phase)}{time_text}"


def _load_json(path: str) -> dict:
    """读取本地产物 JSON（前后端同机部署时直接读文件，信息最全）"""
    if not path or not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def analysis_rows(task: dict) -> list:
    """功能点（含子功能）-> [[id, name, priority]]"""
    full = _load_json(task.get("analysis", {}).get("file_path", ""))
    rows = []

    def _walk(node, level=0):
        rows.append([
            node.get("id", ""),
            ("　" * level) + node.get("name", ""),
            node.get("priority", ""),
        ])
        for sf in node.get("sub_functions", []) or []:
            _walk(sf, level + 1)

    for f in full.get("function_tree", []) or []:
        _walk(f)
    return rows or [["（暂无数据）", "", ""]]


def cases_rows(task: dict) -> list:
    """用例 -> [[id, title, type, steps, expected]]"""
    full = _load_json(task.get("test_suite", {}).get("file_path", ""))
    rows = []
    for c in full.get("test_cases", []) or []:
        steps = c.get("steps", [])
        if isinstance(steps, list):
            steps = "\n".join(
                f"{s.get('step', i+1)}. {s.get('action', '')}"
                if isinstance(s, dict) else str(s)
                for i, s in enumerate(steps)
            )
        expected = ""
        st = c.get("steps", [])
        if isinstance(st, list) and st and isinstance(st[-1], dict):
            expected = st[-1].get("expected", "")
        rows.append([
            c.get("case_id", ""),
            c.get("title", ""),
            c.get("type", "") or c.get("case_type", ""),
            steps or "",
            expected,
        ])
    return rows or [["（暂无数据）", "", "", "", ""]]


def review_summary(review: dict) -> str:
    if not review:
        return "（暂无数据）"
    lines = [f"**评审得分**：{review.get('score', 'N/A')}"]
    passed = review.get("passed", review.get("approved"))
    if passed is not None:
        lines.append(f"**评审结论**：{'✅ 通过' if passed else '❌ 不通过'}")
    for k in ("total_cases", "passed_cases", "failed_cases",
              "coverage_gaps", "feedback"):
        v = review.get(k)
        if v is not None and v != "" and v != []:
            lines.append(f"**{k}**：{v}")
    return "\n\n".join(lines)


def execution_summary(execution: dict) -> str:
    if not execution:
        return "（暂无数据）"
    lines = []
    # 执行模式徽章（语义化：模拟 vs 真实执行）
    mode = execution.get("execution_mode", "")
    if mode == "simulated":
        lines.append("### 🧪 模拟执行（simulated）\n"
                     "> 未连接真实被测系统，结果由执行引擎按规则推演，"
                     "仅用于演示全流程，不代表真实测试结论。\n")
    elif mode == "mcp":
        lines.append("### ⚡ MCP 真实执行\n"
                     "> 通过 MCP Server 调用 Playwright / API Test 真实执行。\n")
    label = {"total": "总用例数", "passed": "通过", "failed": "失败",
             "blocked": "阻塞", "skipped": "跳过",
             "pass_rate": "通过率", "duration_seconds": "耗时(秒)",
             "log_path": "执行日志"}
    for k, v in execution.items():
        if isinstance(v, (str, int, float, bool)):
            lines.append(f"- **{label.get(k, k)}**：{v}")
    return "\n".join(lines) if lines else "（暂无数据）"


# ─────────────────────────────────────────────
# 事件处理
# ─────────────────────────────────────────────

def start_pipeline(prd_file, prd_text, model, execution_mode, mock_llm,
                   max_review_rounds, llm_api_key, llm_base_url, target_url,
                   progress=gr.Progress()):
    """触发流水线并阻塞轮询直到完成（Gradio progress 保持 UI 活跃）"""
    progress(0, desc="提交任务…")
    # 填了 API Key 时强制走真实 LLM（覆盖 Mock）
    if llm_api_key and llm_api_key.strip():
        mock_llm = False
    # mcp 模式必须提供目标地址
    if execution_mode == "mcp" and not (target_url or "").strip():
        raise gr.Error("⚠️ 已选 MCP 真实执行，必须填写被测目标地址")
    try:
        resp = api_start_pipeline(prd_file, prd_text, model, execution_mode,
                                  mock_llm, max_review_rounds,
                                  llm_api_key or "", llm_base_url or "",
                                  target_url or "")
    except ValueError as e:
        raise gr.Error(str(e))
    except Exception as e:
        raise gr.Error(f"提交失败：{e}")

    task_id = resp["task_id"]
    deadline = time.time() + 600  # 10 分钟上限
    last_phase = ""
    while time.time() < deadline:
        task = api_get_task(task_id)
        status, phase = task.get("status"), task.get("phase")
        if phase != last_phase:
            progress(0.1, desc=f"{STATUS_LABELS.get(status, status)} "
                              f"{PHASE_LABELS.get(phase, phase)}")
            last_phase = phase
        if status in ("completed", "failed"):
            break
        time.sleep(2)
    return task_id


def render_results(task_id):
    """任务完成后渲染全部结果"""
    if not task_id:
        return ([["（暂无数据）", "", ""]],
                [["（暂无数据）", "", "", "", ""]],
                "（暂无数据）", "（暂无数据）", "（暂无数据）",
                gr.update(choices=[], value=[]), "（暂无数据）")
    try:
        task = api_get_task(task_id)
    except Exception as e:
        raise gr.Error(f"查询任务失败：{e}")

    a_tbl = analysis_rows(task)
    c_tbl = cases_rows(task)
    rvw = review_summary(task.get("review", {}))
    exe = execution_summary(task.get("execution", {}))

    # 增量更新：功能点复选框
    func_ids = [row[0] for row in a_tbl if row[0]]
    incr_update = gr.update(choices=func_ids, value=[])

    log_text = "\n".join(task.get("messages", [])) or "（无日志）"

    try:
        report_md = api_report_md(task_id)
    except Exception:
        report_md = "（报告尚未生成或任务未完成）"

    return a_tbl, c_tbl, rvw, exe, report_md, incr_update, log_text


def refresh_status(task_id):
    """手动刷新当前任务状态"""
    if not task_id:
        return "未开始", "（无任务）"
    task = api_get_task(task_id)
    status = STATUS_LABELS.get(task.get("status"), task.get("status"))
    return f"{status}", phase_progress(task)


def tick_refresh(task_id, auto_enabled):
    """定时器回调：运行中的任务自动刷新；完成/失败时停表并渲染结果

    返回 (状态, 进度, timer激活, 结果组件们或 None 保持)
    """
    if not task_id or not auto_enabled:
        return gr.skip(), gr.skip(), gr.skip(), \
            gr.skip(), gr.skip(), gr.skip(), gr.skip(), gr.skip(), \
            gr.skip(), gr.skip(), gr.skip()
    try:
        task = api_get_task(task_id)
    except Exception:
        return gr.skip(), gr.skip(), gr.skip(), \
            gr.skip(), gr.skip(), gr.skip(), gr.skip(), gr.skip(), \
            gr.skip(), gr.skip(), gr.skip()
    status = task.get("status")
    if status not in ("running", "pending"):
        # 任务结束：停止定时器，渲染最终结果
        s = STATUS_LABELS.get(status, status)
        return s, phase_progress(task), gr.Timer(active=False), \
            *render_results(task_id)
    s = STATUS_LABELS.get(status, status)
    return s, phase_progress(task), gr.Timer(active=True), \
        gr.skip(), gr.skip(), gr.skip(), gr.skip(), gr.skip(), \
        gr.skip(), gr.skip(), gr.skip()


def do_incremental(base_task_id, selected_ids):
    if not base_task_id:
        raise gr.Error("没有基础任务")
    if not selected_ids:
        raise gr.Error("请至少勾选一个变更功能")
    resp = api_incremental(base_task_id, selected_ids)
    return resp["task_id"]


def load_history_tasks():
    """历史任务 -> Dataframe 数据"""
    tasks = api_list_tasks(20)
    rows = []
    for t in tasks:
        rows.append([
            t.get("task_id", "")[:12],
            STATUS_LABELS.get(t.get("status"), t.get("status")),
            PHASE_LABELS.get(t.get("phase"), t.get("phase")),
            t.get("prd_filename", ""),
            "是" if t.get("incremental") else "否",
            t.get("created_at", ""),
        ])
    return rows or [["（无历史任务）", "", "", "", "", ""]]


def load_history_detail(evt: gr.SelectData, history_data):
    """点击历史任务行 -> 回看结果"""
    rows = history_data if isinstance(history_data, list) else \
        (history_data.values.tolist() if history_data is not None
         and hasattr(history_data, "values") else [])
    if not rows or evt.index[0] >= len(rows):
        return None
    task_id_short = rows[evt.index[0]][0]
    # task_id 截断过，通过列表接口找全
    tasks = api_list_tasks(100)
    full_id = next(
        (t["task_id"] for t in tasks
         if t["task_id"].startswith(task_id_short)), None)
    if not full_id:
        raise gr.Error("任务未找到")
    return full_id


# ─────────────────────────────────────────────
# UI 构建
# ─────────────────────────────────────────────

def check_backend():
    h = api_health()
    if h.get("status") == "ok":
        return f"🟢 后端在线（{API_BASE}，v{h.get('version', '?')}）"
    return f"🔴 后端离线（{API_BASE}）— 请先启动: uvicorn api.main:app --port 8000"


def build_ui() -> gr.Blocks:
    with gr.Blocks(title="iTest-Agent 智能测试平台") as app:
        gr.Markdown("# 🧪 iTest-Agent 智能测试平台")
        gr.Markdown("输入 PRD -> 自动生成测试用例 -> 评审 -> 执行 -> 测试报告")
        backend_status = gr.Markdown(check_backend)

        with gr.Tab("🚀 全流程"):
            with gr.Row():
                with gr.Column(scale=1):
                    gr.Markdown("#### ① 输入 PRD")
                    prd_file = gr.File(
                        label="上传 PRD（.md）",
                        file_types=[".md"],
                    )
                    prd_text = gr.Textbox(
                        label="或粘贴 PRD 文本",
                        placeholder="将 PRD 的 Markdown 内容粘贴到这里…",
                        lines=10,
                    )
                with gr.Column(scale=1):
                    gr.Markdown("#### ② 参数")
                    model_tb = gr.Textbox(
                        label="LLM 模型",
                        value="deepseek-chat",
                        info="DeepSeek: deepseek-chat / deepseek-reasoner",
                    )
                    key_tb = gr.Textbox(
                        label="API Key（留空则走 Mock 规则解析）",
                        type="password",
                        placeholder="sk-...",
                        info="DeepSeek 平台申请；填写后走真实 LLM，不填且未配置服务端 Key 时自动降级 Mock",
                    )
                    baseurl_tb = gr.Textbox(
                        label="API Base URL（OpenAI 兼容接口）",
                        value="https://api.deepseek.com/v1",
                        info="DeepSeek 官方地址已预填，可换成其他兼容服务",
                    )
                    exec_dd = gr.Dropdown(
                        label="执行模式",
                        choices=["simulated", "mcp"],
                        value="simulated",
                        info="simulated=模拟执行（无需环境）；mcp=真实执行",
                    )
                    target_url_tb = gr.Textbox(
                        label="被测目标地址（mcp 必填）",
                        placeholder="如 http://127.0.0.1:8090 或 https://api.example.com",
                        info="选 mcp 时需提供被测系统地址，将注入到未指定 url 的用例；api 用例作为 Base URL",
                    )
                    with gr.Row():
                        target_warn = gr.Markdown(
                            "⚠️ 当前为**模拟执行**，被测目标地址可留空",
                            elem_classes=["mcp-hint"],
                        )
                    mock_cb = gr.Checkbox(
                        label="Mock LLM（无 API Key 离线演示）",
                        value=False,
                    )
                    rounds_sl = gr.Slider(
                        label="评审最大迭代轮数",
                        minimum=1, maximum=10, value=3, step=1,
                    )
                    start_btn = gr.Button("🚀 开始测试", variant="primary")

            # ③ 运行状态：紧凑横条，直接衔接结果区
            task_state = gr.State(None)
            with gr.Row():
                status_md = gr.Markdown("（未开始）", scale=1)
                progress_md = gr.Markdown("（等待任务）", scale=2)
            with gr.Row():
                refresh_btn = gr.Button("🔄 手动刷新状态", size="sm", scale=1)
                auto_cb = gr.Checkbox(
                    label="自动刷新（运行中每 3 秒）", value=True, scale=1)
            # 定时器：运行中的任务自动轮询状态
            status_timer = gr.Timer(value=3, active=False)

        gr.Markdown("### ④ 测试结果")
        with gr.Tab("📋 功能点") as tab_a:
            analysis_df = gr.Dataframe(
                headers=["功能 ID", "功能名称", "优先级"],
                label="需求分析 - 功能点",
                interactive=False,
            )
        with gr.Tab("📝 测试用例") as tab_c:
            cases_df = gr.Dataframe(
                headers=["用例 ID", "标题", "类型", "步骤", "预期结果"],
                label="测试用例",
                interactive=False,
            )
        with gr.Tab("🔍 评审结果") as tab_r:
            review_md = gr.Markdown("（暂无数据）")
        with gr.Tab("⚡ 执行结果") as tab_e:
            exec_md = gr.Markdown("（暂无数据）")
        with gr.Tab("📄 测试报告") as tab_p:
            report_md = gr.Markdown("（暂无数据）")
            with gr.Row():
                dl_md = gr.File(label="下载 Markdown")
                dl_pdf = gr.File(label="下载 PDF")
                dl_json = gr.File(label="下载执行日志 JSON")
        with gr.Tab("🪵 运行日志") as tab_l:
            log_tb = gr.Textbox(label="日志", lines=15, interactive=False)
        with gr.Tab("➕ 增量更新") as tab_i:
            gr.Markdown("勾选发生变更的功能，仅重新生成对应用例：")
            incr_cb = gr.Checkboxgroup(label="变更功能 ID", choices=[])
            incr_btn = gr.Button("➕ 触发增量更新", variant="primary")

        with gr.Tab("🕘 历史任务"):
            history_df = gr.Dataframe(
                headers=["任务 ID", "状态", "阶段", "PRD 文件",
                         "增量", "创建时间"],
                label="最近任务（点击行回看结果）",
                interactive=False,
            )
            history_state = gr.State([])
            history_btn = gr.Button("🔄 刷新历史")
            gr.Markdown("点击任务行后，结果将加载到上方「测试结果」区域。")

        gr.Markdown("---\n*iTest-Agent · LangGraph 多 Agent 智能测试系统 · "
                    f"后端 {API_BASE}*")

        # ── 事件绑定 ──
        start_btn.click(
            fn=start_pipeline,
            inputs=[prd_file, prd_text, model_tb, exec_dd, mock_cb,
                    rounds_sl, key_tb, baseurl_tb, target_url_tb],
            outputs=task_state,
        ).then(
            fn=refresh_status,
            inputs=task_state,
            outputs=[status_md, progress_md],
        ).then(
            fn=lambda tid, auto: gr.Timer(active=True)
            if (tid and auto) else gr.Timer(active=False),
            inputs=[task_state, auto_cb],
            outputs=[status_timer],
        )

        # 执行模式联动：mcp 时提示必填（改文案 + 可见性）
        exec_dd.change(
            fn=lambda mode: gr.update(
                visible=True,
                value=("⚠️ 已选 **MCP 真实执行**，被测目标地址必填，将从该地址启动页面/请求"
                       if mode == "mcp"
                       else "⚠️ 当前为**模拟执行**，被测目标地址可留空"),
            ),
            inputs=[exec_dd],
            outputs=[target_warn],
        )

        # 定时器：任务运行中每 3s 刷新；结束时停表并渲染结果
        status_timer.tick(
            fn=tick_refresh,
            inputs=[task_state, auto_cb],
            outputs=[status_md, progress_md, status_timer,
                     analysis_df, cases_df, review_md, exec_md, report_md,
                     incr_cb, log_tb],
        )

        refresh_btn.click(
            fn=refresh_status,
            inputs=task_state,
            outputs=[status_md, progress_md],
        )

        # 报告下载（任务完成后点击）
        tab_p.select(
            fn=lambda tid: api_report_file(tid, "md") if tid else None,
            inputs=task_state, outputs=dl_md,
        )
        # PDF/JSON 通过按钮式交互：用 report tab 的二次点击简化为全部加载
        tab_p.select(
            fn=lambda tid: (api_report_file(tid, "pdf") if tid else None,
                            api_report_file(tid, "json") if tid else None),
            inputs=task_state, outputs=[dl_pdf, dl_json],
        )

        incr_btn.click(
            fn=do_incremental,
            inputs=[task_state, incr_cb],
            outputs=task_state,
        ).then(
            fn=render_results,
            inputs=task_state,
            outputs=[analysis_df, cases_df, review_md, exec_md, report_md,
                     incr_cb, log_tb],
        ).then(
            fn=refresh_status,
            inputs=task_state,
            outputs=[status_md, progress_md],
        )

        history_btn.click(
            fn=lambda: (rows := load_history_tasks(), rows)[1],
            outputs=[history_df],
        ).then(
            fn=lambda rows: rows,
            inputs=history_df,
            outputs=history_state,
        )

        history_df.select(
            fn=load_history_detail,
            inputs=[history_state],
            outputs=task_state,
        ).then(
            fn=render_results,
            inputs=task_state,
            outputs=[analysis_df, cases_df, review_md, exec_md, report_md,
                     incr_cb, log_tb],
        ).then(
            fn=refresh_status,
            inputs=task_state,
            outputs=[status_md, progress_md],
        )

        # 页面加载时拉取历史
        app.load(
            fn=lambda: (rows := load_history_tasks(), rows)[1],
            outputs=[history_df],
        ).then(
            fn=lambda rows: rows,
            inputs=history_df,
            outputs=history_state,
        )

    return app


if __name__ == "__main__":
    import logging
    logging.getLogger("httpx").setLevel(logging.WARNING)
    build_ui().launch(
        server_name="127.0.0.1",
        server_port=int(os.getenv("ITEST_UI_PORT", "7860")),
        show_error=True,
        inbrowser=True,
    )

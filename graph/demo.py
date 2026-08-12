"""
iTest-Agent LangGraph 状态图演示脚本

演示核心工作流：
1. 需求分析 → 2. 用例生成 → 3. 用例评审 → 4. 分支判断 → 5. 执行/重生成

用法:
    python graph/demo.py                          # 使用默认 sample PRD 运行完整流程
    python graph/demo.py --prd <path>             # 指定 PRD 路径
    python graph/demo.py --incremental             # 增量更新模式
    python graph/demo.py --checkpoint <db_path>    # 指定 checkpoint 文件
"""

import argparse
import json
import os
import sys

# 添加项目根目录到 Python 路径
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _project_root)

from graph.state import create_initial_state, create_incremental_state
from graph.workflow import build_itest_workflow


def main():
    parser = argparse.ArgumentParser(
        description="iTest-Agent LangGraph 状态图演示"
    )
    parser.add_argument(
        "--prd",
        default=None,
        help="PRD 文档路径（默认使用 tests/sample_prd.md）",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="输出目录",
    )
    parser.add_argument(
        "--checkpoint-db",
        default=None,
        help="Checkpoint SQLite 路径",
    )
    parser.add_argument(
        "--model",
        default="gpt-4o-mini",
        help="LLM 模型名称",
    )
    parser.add_argument(
        "--incremental",
        action="store_true",
        help="增量更新模式（需额外提供 --prev-analysis）",
    )
    parser.add_argument(
        "--prev-analysis",
        default=None,
        help="增量模式下的前次分析结果 JSON 路径",
    )
    parser.add_argument(
        "--changed-functions",
        default=None,
        help="增量模式下变更的功能 ID 列表，逗号分隔",
    )
    parser.add_argument(
        "--no-llm",
        action="store_true",
        help="跳过 LLM 调用（仅验证图拓扑）",
    )
    args = parser.parse_args()

    # 确定 PRD 路径
    prd_path = args.prd or os.path.join(_project_root, "tests", "sample_prd.md")
    if not os.path.exists(prd_path):
        print(f"[错误] PRD 文件不存在: {prd_path}")
        sys.exit(1)

    # 确定输出目录
    output_dir = args.output_dir or os.path.join(os.path.dirname(prd_path), "output")
    os.makedirs(output_dir, exist_ok=True)

    # 确定 checkpoint 路径
    checkpoint_db = args.checkpoint_db or os.path.join(output_dir, "itest_checkpoints.db")

    # 知识库目录
    kb_dir = os.path.join(_project_root, "chroma_db")

    print("=" * 60)
    print("  iTest-Agent LangGraph 状态图演示")
    print("=" * 60)
    print(f"  PRD 路径:        {prd_path}")
    print(f"  输出目录:        {output_dir}")
    print(f"  Checkpoint DB:   {checkpoint_db}")
    print(f"  LLM 模型:        {args.model}")
    print(f"  增量模式:        {'是' if args.incremental else '否'}")
    if args.no_llm:
        print(f"  LLM 调用:        跳过（仅验证图拓扑）")
    print("=" * 60)
    print()

    if args.no_llm:
        # 仅构建图，不调用 LLM
        print("[跳过] 仅验证 StateGraph 拓扑 — 不执行实际 LLM 调用")
        from graph.workflow import build_state_graph
        graph = build_state_graph()
        print(f"[OK] StateGraph 构建成功，节点数: {len(graph.nodes)}")
        print(f"[OK] 节点列表: {list(graph.nodes.keys())}")
        return

    # 构建工作流
    workflow = build_itest_workflow(checkpoint_db_path=checkpoint_db)

    # 创建初始状态
    if args.incremental and args.prev_analysis:
        changed = []
        if args.changed_functions:
            changed = [f.strip() for f in args.changed_functions.split(",") if f.strip()]

        initial_state = create_incremental_state(
            prd_path=prd_path,
            previous_analysis_path=args.prev_analysis,
            changed_function_ids=changed,
            llm_model=args.model,
            output_dir=output_dir,
            kb_persist_dir=kb_dir,
            checkpoint_db_path=checkpoint_db,
        )
        print("[模式] 增量更新")
    else:
        initial_state = create_initial_state(
            prd_path=prd_path,
            llm_model=args.model,
            output_dir=output_dir,
            kb_persist_dir=kb_dir,
            checkpoint_db_path=checkpoint_db,
        )

    # 执行工作流（流式输出状态快照）
    config = {"configurable": {"thread_id": "demo-session-1"}}

    print("开始执行工作流...\n")

    try:
        step = 0
        for event in workflow.run_stream(initial_state, config=config):
            step += 1
            # 每个 event 是一个字典 {node_name: state_update}
            for node_name, state_update in event.items():
                phase = state_update.get("phase", "?")
                msgs = state_update.get("messages", [])
                last_msg = msgs[-1] if msgs else "(无消息)"
                print(f"  [{step}] 节点 {node_name:<25s} | 阶段: {phase:<12s} | {last_msg}")

        print("\n" + "=" * 60)
        print("  工作流执行完成!")
        print("=" * 60)

        # 输出最终结果汇总
        final_state = workflow.get_state(config)
        print(f"\n最终状态摘要:")
        analysis = final_state.get("analysis_result", {})
        if analysis:
            print(f"  产品: {analysis.get('product_name')}")
            print(f"  功能: {analysis.get('total_functions', 0)} 个")
        suite = final_state.get("test_suite", {})
        if suite:
            print(f"  用例: {suite.get('total_cases', 0)} 条")
            # 追溯矩阵信息
            tmx_path = suite.get("traceability_matrix_path", "")
            if tmx_path:
                print(f"  追溯矩阵: {tmx_path}")
                # 尝试读取覆盖率
                try:
                    with open(tmx_path, "r", encoding="utf-8") as f:
                        tmx_data = json.load(f)
                    summary = tmx_data.get("summary", {})
                    if summary:
                        print(f"    功能点总数: {summary.get('total_functions', 'N/A')}")
                        print(f"    已关联: {summary.get('linked_functions', 'N/A')}")
                        print(f"    未关联: {summary.get('unlinked_functions', 'N/A')}")
                except Exception:
                    pass
        review = final_state.get("review_result", {})
        if review:
            print(f"  评审: {'通过' if review.get('passed') else '不通过'} "
                  f"(评分 {review.get('score', 0):.1f})")
        report = final_state.get("report_path", "")
        if report:
            print(f"  报告: {report}")

    except Exception as e:
        print(f"\n[错误] 工作流执行失败: {e}")
        import traceback
        traceback.print_exc()

        # 尝试从 checkpoint 查看状态
        try:
            state = workflow.app.get_state(config)
            if state:
                print(f"\n[Checkpoint] 中断前状态: phase={state.values.get('phase')}")
        except Exception:
            pass
        sys.exit(1)


if __name__ == "__main__":
    main()

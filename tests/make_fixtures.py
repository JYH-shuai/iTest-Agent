"""
生成 tests/output/ 下的测试 fixture 产物（执行日志 / 用例集 / 评审结果）。

用法：
    python tests/make_fixtures.py

说明：
    通过 Mock 流水线（不依赖 API Key）完整跑一遍 sample PRD，
    把产物复制到 tests/output/，供 TestReportGeneratorE2E 等测试使用。
    tests/output/ 被 .gitignore 排除，属运行时生成物。
"""

import json
import os
import shutil
import sys
import time

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

FIXTURE_DIR = os.path.join(_PROJECT_ROOT, "tests", "output")
SAMPLE_PRD = os.path.join(_PROJECT_ROOT, "tests", "sample_prd.md")


def main() -> int:
    from api.main import TASK_STORE
    from api.task_store import TaskStore
    from graph.state import create_initial_state
    from graph.workflow import build_itest_workflow

    output_dir = os.path.join(_PROJECT_ROOT, "output", "_fixture_gen")
    os.makedirs(output_dir, exist_ok=True)

    state = create_initial_state(
        prd_path=SAMPLE_PRD,
        llm_model="gpt-4o-mini",
        output_dir=output_dir,
        kb_persist_dir=os.path.join(_PROJECT_ROOT, "chroma_db"),
        checkpoint_db_path=os.path.join(output_dir, "itest_checkpoints.db"),
        config={"execution_mode": "simulated", "max_review_rounds": 3},
        mock_llm=True,
    )

    workflow = build_itest_workflow(
        checkpoint_db_path=os.path.join(output_dir, "itest_checkpoints.db")
    )
    config = {"configurable": {"thread_id": "fixture-gen"}}
    merged: dict = {}
    for event in workflow.app.stream(state, config=config):
        for _node, delta in event.items():
            if isinstance(delta, dict):
                merged.update(delta)

    exec_log = merged.get("execution_result", {}).get("log_path", "")
    suite = merged.get("test_suite", {}) or {}
    suite_path = suite.get("file_path", "")
    if not exec_log or not suite_path or not os.path.exists(exec_log):
        print("[ERROR] 流水线产物缺失，生成失败")
        return 1

    os.makedirs(FIXTURE_DIR, exist_ok=True)
    shutil.copy(exec_log, os.path.join(FIXTURE_DIR, "execution_log.json"))
    shutil.copy(suite_path, os.path.join(FIXTURE_DIR, "test_suite.json"))
    # review_result.json 从 state 提取
    review = merged.get("review_result", {}) or {}
    if review:
        with open(os.path.join(FIXTURE_DIR, "review_result.json"), "w") as f:
            json.dump(review, f, ensure_ascii=False, indent=2)

    _inject_failure_scenario(FIXTURE_DIR)

    print(f"[OK] fixtures 已生成到 {FIXTURE_DIR}")
    return 0


def _inject_failure_scenario(fixture_dir: str) -> None:
    """将执行日志改为 12 条用例 / 7 过 5 败（58.3%），
    覆盖报告生成器对失败用例与缺陷聚类的处理路径。

    测试断言依赖：TC-FUNC-003-02-01 为失败用例、通过率 58.3%。
    """
    exec_path = os.path.join(fixture_dir, "execution_log.json")
    with open(exec_path, encoding="utf-8") as f:
        log = json.load(f)

    details = log.get("details", [])
    if len(details) > 12:
        details = details[:12]

    failed_ids = {
        "TC-FUNC-002-01-01", "TC-FUNC-003-01-01", "TC-FUNC-003-02-01",
        "TC-FUNC-003-03-01", "TC-FUNC-004-01-01",
    }
    passed, failed = 0, 0
    for i, det in enumerate(details):
        cid = det.get("case_id", f"TC-FUNC-000-{i:02d}")
        if cid in failed_ids:
            det["status"] = "failed"
            det["details"] = {
                "kind": "assert",
                "error": "断言失败: 预期提示文案与实际不符（模拟注入）",
            }
            failed += 1
        else:
            det["status"] = "passed"
            passed += 1
    total = len(details)
    log["total"] = total
    log["passed"] = passed
    log["failed"] = failed
    log["blocked"] = 0
    log["skipped"] = 0
    log["pass_rate"] = round(passed / total * 100, 1) if total else 0.0
    log["details"] = details

    with open(exec_path, "w", encoding="utf-8") as f:
        json.dump(log, f, ensure_ascii=False, indent=2)
    print(f"  [fixture] 注入失败场景: {total} 条 / 通过 {passed} 失败 {failed}")


if __name__ == "__main__":
    sys.exit(main())

"""
运行 Demo 用例集（MCP 真实执行）并生成含缺陷聚类的报告

用法：
    python docker/run_demo_cases.py

前置：
    1. python docker/demo_app.py            （被测系统 :8090）
    2. playwright install chromium           （浏览器已安装）

输出：
    output/demo_execution_log.json          （执行日志）
    output/demo_test_report.md              （含缺陷聚类报告）
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from docker.demo_cases import write_suite  # noqa: E402
from execution.engine import ExecutionEngine  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT_DIR = os.path.join(ROOT, "output")


def main() -> int:
    suite_path = write_suite(os.path.join(OUTPUT_DIR, "demo_suite.json"))
    with open(suite_path, encoding="utf-8") as f:
        suite = json.load(f)

    engine = ExecutionEngine(mode="mcp")
    results = []
    for case in suite["test_cases"]:
        print(f"\n▶ {case['case_id']} {case['title']}")
        result = engine.execute_case(case)
        status = result.get("status", "unknown")
        mark = "✅" if status == "passed" else "❌"
        print(f"  {mark} {status} | mode={result.get('mode', '?')}")
        if result.get("details"):
            det = result["details"]
            if "actions" in det:
                for a in det["actions"][-3:]:
                    print(f"     - {a.get('action', '')}: {str(a.get('note', a.get('text', '')))[:60]}")
            if "assertions" in det:
                for a in det["assertions"]:
                    print(f"     - assert {a.get('selector', '')}: {a.get('expected', '')} => "
                          f"{'PASS' if a.get('passed') else 'FAIL'} (actual: {str(a.get('actual', ''))[:40]})")
        results.append({"case_id": case["case_id"], "title": case["title"], **result})

    # ── 汇总执行日志 ──
    total = len(results)
    passed = sum(1 for r in results if r.get("status") == "passed")
    failed = total - passed
    log_path = os.path.join(OUTPUT_DIR, "demo_execution_log.json")
    log = {
        "total": total,
        "passed": passed,
        "failed": failed,
        "blocked": 0,
        "skipped": 0,
        "pass_rate": round(passed / total * 100, 1) if total else 0.0,
        "duration_seconds": 0.0,
        "execution_mode": "mcp",
        "details": results,
        "timestamp": "",
    }
    with open(log_path, "w", encoding="utf-8") as f:
        json.dump(log, f, ensure_ascii=False, indent=2)

    # ── 缺陷聚类报告 ──
    report_path = _build_defect_report(results, log, log_path)

    print(f"\n{'=' * 50}")
    print(f"执行汇总: {passed}/{total} 通过 | 通过率 {log['pass_rate']}%")
    print(f"执行日志: {log_path}")
    print(f"缺陷报告: {report_path}")
    print("=" * 50)
    return 0


def _build_defect_report(results, log, log_path):
    """从失败用例生成缺陷聚类报告（按失败原因聚类）"""
    import re
    from collections import defaultdict

    clusters: dict = defaultdict(list)
    for r in results:
        if r.get("status") != "failed":
            continue
        details = r.get("details", {})
        reason = "未知原因"
        if "assertions" in details:
            failed_asserts = [a for a in details["assertions"] if not a.get("passed")]
            if failed_asserts:
                a = failed_asserts[0]
                actual = str(a.get("actual", ""))[:50]
                # 超时/不可见视为"无提示"，给出人类可读的缺陷描述
                if "Error calling tool" in actual or "Timeout" in actual:
                    actual = "（页面无任何提示/元素不可见）"
                reason = f"断言失败: 期望「{a.get('expected', '')}」, 实际「{actual}」"
        elif "actions" in details:
            last = details["actions"][-1] if details["actions"] else {}
            reason = f"执行异常: {str(last.get('note', last.get('error', '')))[:80]}"
        clusters[reason].append(r["case_id"])

    lines = [
        "# iTest-Agent Demo 缺陷聚类报告",
        "",
        f"**执行时间**: {log.get('timestamp', '')}  ",
        f"**执行模式**: MCP 真实执行（Playwright）  ",
        f"**结果**: {log.get('passed', 0)}/{log.get('total', 0)} 通过，通过率 {log.get('pass_rate', 0)}%",
        "",
        f"**缺陷数**: {len(clusters)} 类（共 {sum(len(v) for v in clusters.values())} 条失败用例）",
        "",
    ]
    for i, (reason, case_ids) in enumerate(clusters.items(), 1):
        lines.append(f"## 缺陷 {i}")
        lines.append(f"- **现象**: {reason}")
        lines.append(f"- **影响用例**: {', '.join(case_ids)}")
        lines.append("")

    report_path = os.path.join(OUTPUT_DIR, "demo_test_report.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return report_path


if __name__ == "__main__":
    sys.exit(main())

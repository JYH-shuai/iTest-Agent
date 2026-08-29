"""
LLM-as-a-Judge 评估器 — 用独立 LLM 作为裁判，对测试报告/产物进行质量评估。

用途（阶段四·演示传播）：
    生成一份「测试报告质量评估」——用另一个 LLM（默认 DeepSeek）作为裁判，
    对报告从多个维度打分，输出结构化 JSON 评分 + 改进建议。

设计：
- 默认可配置 Judge 模型（DeepSeek），无 LLM Key 时降级为规则打分
- 输出结构化评分（各维度/总分/评级）+ 改进建议
- 评估对象：测试报告 Markdown（执行日志 + 缺陷聚类）

用法：
    python -m execution.llm_judge --report output/demo_test_report.md
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict, List, Optional

# 项目根目录
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


# 评估维度定义
DIMENSIONS = [
    {
        "key": "completeness",
        "name": "完整性",
        "desc": "报告是否覆盖测试摘要、执行统计、用例明细、缺陷聚类等核心章节",
    },
    {
        "key": "traceability",
        "name": "可追溯性",
        "desc": "缺陷/用例是否关联具体 ID（如 TC-xxx / BUG-xxx），能否定位到源头",
    },
    {
        "key": "defect_clarity",
        "name": "缺陷识别清晰度",
        "desc": "缺陷描述是否准确、现象是否具体、能否指导修复",
    },
    {
        "key": "readability",
        "name": "可读性",
        "desc": "结构是否清晰、排版是否友好、信息是否突出",
    },
    {
        "key": "actionability",
        "name": "可执行性",
        "desc": "报告能否直接指导后续动作（修复缺陷/补用例/优化流程）",
    },
]

# 评分量表
SCORE_RUBRIC = {
    "1": "极差，缺失关键信息",
    "2": "较差，信息不完整",
    "3": "合格，基本满足",
    "4": "良好，结构清晰",
    "5": "优秀，专业详尽",
}

_JUDGE_SYSTEM = (
    "你是资深的软件测试专家与质量评审员。请对下面这份测试报告进行质量评估。"
    "评估标准要严格、客观、可复制。给出每个维度的 1-5 分和简明理由，"
    "并给出总分（加权平均）和总体评级。只输出 JSON。"
)

_JUDGE_USER_TEMPLATE = """
请评估以下测试报告，并按指定 JSON 格式输出：

报告标题：{title}
内容：
{report}

评估维度（每个 1-5 分）：
{criteria}

输出 JSON 格式（严格遵循）：
{{
  "scores": {{
    "completeness": {{"score": 1-5, "reason": "..."}},
    "traceability": {{"score": 1-5, "reason": "..."}},
    "defect_clarity": {{"score": 1-5, "reason": "..."}},
    "readability": {{"score": 1-5, "reason": "..."}},
    "actionability": {{"score": 1-5, "reason": "..."}}
  }},
  "total_score": 0-5,
  "rating": "A/B/C/D",
  "strengths": ["亮点1", "亮点2"],
  "weaknesses": ["不足1", "不足2"],
  "improvements": ["建议1", "建议2"]
}}
"""


def _load_report(path: str) -> Dict[str, Any]:
    """读取报告文件，返回 {title, text}"""
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()
    title = text.splitlines()[0].lstrip("# ").strip() if text else "（无标题）"
    return {"title": title, "text": text}


def _criteria_text() -> str:
    return "\n".join(f"- {d['key']}（{d['name']}）: {d['desc']}" for d in DIMENSIONS)


def _parse_judge_response(raw: str) -> Optional[Dict[str, Any]]:
    """解析 LLM 返回的 JSON（容错处理 markdown 代码块包围）"""
    text = raw.strip()
    # 去掉 ```json ... ``` 围栏
    if "```" in text:
        start = text.find("```")
        end = text.rfind("```")
        if end > start:
            text = text[start + 3:end]
            # 去掉可能的 json 标识
            text = text.lstrip("json").strip()
    # 定位 JSON 对象
    obj_start = text.find("{")
    obj_end = text.rfind("}")
    if obj_start != -1 and obj_end > obj_start:
        text = text[obj_start:obj_end + 1]
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        print(f"[judge] LLM 返回 JSON 解析失败: {e}")
        return None


def _rule_based_judge(report_text: str) -> Dict[str, Any]:
    """无 LLM 时的规则降级评分（启发式）"""
    import re
    scores: Dict[str, Dict[str, Any]] = {}
    full = report_text

    # 1. 完整性：检查核心章节关键词
    sections = {
        "completeness": ["摘要", "通过率", "执行", "用例", "缺陷", "明细"],
        "traceability": ["TC-", "BUG-", "FR-", "case_id", "缺陷"],
        "defect_clarity": ["实际", "期望", "断言失败", "现象"],
        "readability": ["##", "- ", "**", "\n\n"],
        "actionability": ["建议", "修复", "补", "优化", "下一步"],
    }
    for key in DIMENSIONS:
        k = sections[key["key"]]
        hit = sum(1 for kw in k if kw in full)
        ratio = hit / len(k)
        score = 1 + round(min(ratio * 4, 4))  # 映射到 1-5
        scores[key["key"]] = {
            "score": score,
            "reason": f"规则匹配：关键词命中 {hit}/{len(k)}",
        }

    total = round(sum(s["score"] for s in scores.values()) / len(scores), 1)
    rating = "A" if total >= 4.5 else "B" if total >= 3.5 else "C" if total >= 2.5 else "D"
    return {
        "scores": scores,
        "total_score": total,
        "rating": rating,
        "strengths": [],
        "weaknesses": [],
        "improvements": [],
        "judge_mode": "rule_based",
    }


def _llm_judge(
    title: str,
    report_text: str,
    api_key: Optional[str],
    base_url: Optional[str],
    model: str,
) -> Optional[Dict[str, Any]]:
    """用 LLM 作为裁判评估报告"""
    if not api_key or not base_url:
        return None

    from openai import OpenAI

    client = OpenAI(api_key=api_key, base_url=base_url)
    user_prompt = _JUDGE_USER_TEMPLATE.format(
        title=title,
        report=report_text,
        criteria=_criteria_text(),
    )
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _JUDGE_SYSTEM},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.1,
            max_tokens=2000,
        )
        raw = resp.choices[0].message.content or ""
        parsed = _parse_judge_response(raw)
        if parsed is None:
            return None
        parsed["judge_mode"] = "llm"
        parsed["judge_model"] = model
        return parsed
    except Exception as e:  # noqa: BLE001
        print(f"[judge] LLM 调用失败: {e}")
        return None


def judge_report(
    report_path: str,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    model: str = "deepseek-chat",
) -> Dict[str, Any]:
    """主入口：评估一个测试报告"""
    report = _load_report(report_path)

    # 尝试 LLM 裁判
    result = _llm_judge(report["title"], report["text"], api_key, base_url, model)

    # 降级：规则打分
    if result is None:
        result = _rule_based_judge(report["text"])

    result["report_title"] = report["title"]
    result["report_path"] = report_path
    result["dimensions"] = [
        {"key": d["key"], "name": d["name"], "desc": d["desc"]} for d in DIMENSIONS
    ]
    return result


def print_report(result: Dict[str, Any]) -> None:
    """美化输出评估结果"""
    print("\n" + "=" * 60)
    print(f"LLM-as-a-Judge 评估报告")
    print("=" * 60)
    print(f"报告: {result['report_title']}")
    print(f"模式: {result['judge_mode']}")
    print(f"总评: {result['total_score']}/5.0 (评级 {result['rating']})")
    print("-" * 60)
    for d in DIMENSIONS:
        s = result["scores"].get(d["key"], {})
        print(f"  {d['name']} ({d['key']}): {s.get('score')}/5 — {s.get('reason', '')}")
    if result.get("strengths"):
        print("-" * 60)
        print("💪 亮点:")
        for x in result["strengths"]:
            print(f"  - {x}")
    if result.get("weaknesses"):
        print("⚠️  不足:")
        for x in result["weaknesses"]:
            print(f"  - {x}")
    if result.get("improvements"):
        print("🔧 改进建议:")
        for x in result["improvements"]:
            print(f"  - {x}")
    print("=" * 60)


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="LLM-as-a-Judge 测试报告质量评估")
    parser.add_argument("--report", required=True, help="测试报告 Markdown 路径")
    parser.add_argument("--model", default="deepseek-chat", help="Judge 模型名")
    parser.add_argument("--api-key", default=os.getenv("OPENAI_API_KEY"))
    parser.add_argument("--base-url", default=os.getenv("OPENAI_BASE_URL", "https://api.deepseek.com/v1"))
    parser.add_argument("--json", action="store_true", help="输出原始 JSON")
    args = parser.parse_args()

    result = judge_report(
        args.report,
        api_key=args.api_key,
        base_url=args.base_url,
        model=args.model,
    )
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print_report(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""
LLM-as-a-Judge 评估器单元测试

覆盖：规则降级评分、JSON 解析容错、维度完整性。
不调用真实 LLM（避免 API 依赖），通过 _rule_based_judge / _parse_judge_response 测试。
"""
import pytest

from execution.llm_judge import (
    DIMENSIONS,
    _criteria_text,
    _load_report,
    _parse_judge_response,
    _rule_based_judge,
    judge_report,
)


class TestLoadReport:
    def test_load_report_returns_title_and_text(self, tmp_path):
        p = tmp_path / "report.md"
        p.write_text("# 我的报告\n\n正文内容", encoding="utf-8")
        report = _load_report(str(p))
        assert report["title"] == "我的报告"
        assert "正文内容" in report["text"]


class TestCriteria:
    def test_criteria_covers_all_dimensions(self):
        """评估维度文本应包含全部 5 个维度的 key"""
        text = _criteria_text()
        for d in DIMENSIONS:
            assert d["key"] in text
            assert d["name"] in text


class TestRuleBasedJudge:
    def test_rule_based_returns_all_dimensions(self):
        """规则降级应返回全部维度的分数"""
        text = (
            "# 报告\n\n## 测试摘要\n通过率 100% 用例明细 缺陷 TC-FUNC-001 "
            "断言失败 期望 实际\n\n**表格**\n\n建议 修复 优化 下一步"
        )
        result = _rule_based_judge(text)
        assert set(result["scores"].keys()) == {d["key"] for d in DIMENSIONS}
        assert 1 <= result["total_score"] <= 5
        assert result["judge_mode"] == "rule_based"

    def test_scores_within_range(self):
        """各维度分数应在 1-5 之间"""
        text = "报告内容"
        result = _rule_based_judge(text)
        for s in result["scores"].values():
            assert 1 <= s["score"] <= 5


class TestParseJudgeResponse:
    def test_parse_markdown_fenced_json(self):
        """能容错解析 ```json 围栏包裹的返回"""
        raw = '```json\n{"scores": {"completeness": {"score": 4, "reason": "好"}}, "total_score": 4, "rating": "B"}\n```'
        parsed = _parse_judge_response(raw)
        assert parsed is not None
        assert parsed["scores"]["completeness"]["score"] == 4

    def test_parse_plain_json(self):
        raw = '{"total_score": 3, "rating": "C"}'
        parsed = _parse_judge_response(raw)
        assert parsed is not None
        assert parsed["total_score"] == 3

    def test_parse_invalid_returns_none(self):
        assert _parse_judge_response("这不是 JSON") is None


class TestJudgeReportFallback:
    def test_judge_report_without_api_key_uses_rules(self, tmp_path):
        """无 LLM Key 时降级为规则打分"""
        p = tmp_path / "report.md"
        p.write_text(
            "# 报告\n\n## 测试摘要\n通过率 100%\n缺陷 TC-001 断言失败 建议",
            encoding="utf-8",
        )
        result = judge_report(str(p), api_key=None, base_url=None, use_env_key=False)
        assert result["judge_mode"] == "rule_based"
        assert "total_score" in result
        assert result["report_path"] == str(p)


class TestDimensionsOrdering:
    def test_dimensions_has_five(self):
        assert len(DIMENSIONS) == 5

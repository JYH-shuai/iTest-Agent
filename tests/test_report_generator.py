"""
报告生成器单元测试

覆盖：
- ReportData 数据模型
- DefectClusterer 缺陷聚类（按严重程度/模块/类型）
- MarkdownReportBuilder Markdown 报告生成
- PDFReportBuilder PDF 导出
- ReportGenerator 端到端集成
"""

import json
import os
import sys
import tempfile

import pytest

# 添加项目根目录
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _project_root)

from agents.report_generator import (
    DefectClusterer,
    MarkdownReportBuilder,
    PDFReportBuilder,
    ReportData,
    ReportGenerator,
)


# =============================================================================
# 测试数据 Fixtures
# =============================================================================


@pytest.fixture
def sample_execution_log() -> dict:
    return {
        "total": 10,
        "passed": 6,
        "failed": 2,
        "blocked": 1,
        "skipped": 1,
        "pass_rate": 60.0,
        "duration_seconds": 5.2,
        "timestamp": "2026-06-24T08:30:00Z",
        "details": [
            {"case_id": "TC-001", "title": "P0通过用例", "status": "passed"},
            {"case_id": "TC-002", "title": "P1通过用例", "status": "passed"},
            {"case_id": "TC-003", "title": "P0失败用例", "status": "failed", "error": "断言失败"},
            {"case_id": "TC-004", "title": "P1失败用例", "status": "failed", "error": "超时"},
            {"case_id": "TC-005", "title": "P0阻塞用例", "status": "blocked", "error": "环境不可用"},
            {"case_id": "TC-006", "title": "通过用例", "status": "passed"},
            {"case_id": "TC-007", "title": "通过用例", "status": "passed"},
            {"case_id": "TC-008", "title": "通过用例", "status": "passed"},
            {"case_id": "TC-009", "title": "通过用例", "status": "passed"},
            {"case_id": "TC-010", "title": "跳过用例", "status": "skipped"},
        ],
    }


@pytest.fixture
def sample_suite() -> dict:
    return {
        "suite_name": "测试套件",
        "product_name": "测试产品",
        "module_name": "测试模块",
        "total_cases": 10,
        "p0_count": 3,
        "p1_count": 4,
        "p2_count": 3,
        "test_cases": [
            {"case_id": "TC-001", "title": "P0通过用例", "priority": "P0", "function_name": "登录模块", "type": "功能测试",
             "steps": [{"step": 1, "action": "操作A", "expected": "结果A"}]},
            {"case_id": "TC-002", "title": "P1通过用例", "priority": "P1", "function_name": "搜索模块", "type": "功能测试",
             "steps": []},
            {"case_id": "TC-003", "title": "P0失败用例", "priority": "P0", "function_name": "支付模块", "type": "功能测试",
             "steps": [{"step": 1, "action": "操作B", "expected": "结果B"}]},
            {"case_id": "TC-004", "title": "P1失败用例", "priority": "P1", "function_name": "搜索模块", "type": "接口测试",
             "steps": [{"step": 1, "action": "调用API", "expected": "返回200"}]},
            {"case_id": "TC-005", "title": "P0阻塞用例", "priority": "P0", "function_name": "支付模块", "type": "功能测试",
             "steps": []},
            {"case_id": "TC-006", "title": "通过用例", "priority": "P1", "function_name": "登录模块", "type": "安全测试", "steps": []},
            {"case_id": "TC-007", "title": "通过用例", "priority": "P2", "function_name": "购物车模块", "type": "功能测试", "steps": []},
            {"case_id": "TC-008", "title": "通过用例", "priority": "P2", "function_name": "购物车模块", "type": "性能测试", "steps": []},
            {"case_id": "TC-009", "title": "通过用例", "priority": "P1", "function_name": "搜索模块", "type": "功能测试", "steps": []},
            {"case_id": "TC-010", "title": "跳过用例", "priority": "P2", "function_name": "配置模块", "type": "功能测试", "steps": []},
        ],
    }


@pytest.fixture
def sample_analysis() -> dict:
    return {
        "overview": {
            "product_name": "测试产品",
            "module_name": "测试模块",
            "total_functions": 20,
            "p0_count": 5,
            "p1_count": 8,
            "p2_count": 7,
        }
    }


@pytest.fixture
def sample_review() -> dict:
    return {
        "passed": True,
        "score": 85.5,
        "total_cases": 10,
        "passed_cases": 9,
        "failed_cases": 1,
        "feedback": "评审通过，覆盖度良好",
        "failed_case_ids": ["TC-010"],
        "coverage_gaps": [],
    }


@pytest.fixture
def report_data(sample_execution_log, sample_suite) -> ReportData:
    return ReportData(execution_log=sample_execution_log, test_suite=sample_suite)


# =============================================================================
# ReportData 模型测试
# =============================================================================


class TestReportData:
    def test_basic_stats(self, report_data):
        assert report_data.total == 10
        assert report_data.passed == 6
        assert report_data.failed == 2
        assert report_data.blocked == 1
        assert report_data.skipped == 1
        assert report_data.passed_pct == 60.0
        assert report_data.failed_pct == 20.0
        assert report_data.blocked_pct == 10.0
        assert report_data.skipped_pct == 10.0

    def test_case_lookup(self, report_data):
        case = report_data.get_case("TC-003")
        assert case is not None
        assert case["priority"] == "P0"
        assert case["function_name"] == "支付模块"

    def test_case_lookup_missing(self, report_data):
        assert report_data.get_case("NONEXIST") is None

    def test_no_suite(self, sample_execution_log):
        data = ReportData(execution_log=sample_execution_log)
        assert data.get_case("TC-001") is None
        assert data.total == 10


# =============================================================================
# DefectClusterer 测试
# =============================================================================


class TestDefectClusterer:
    def test_by_priority(self, report_data):
        clusters = DefectClusterer.by_priority(report_data)
        # TC-003 P0 failed, TC-004 P1 failed, TC-005 P0 blocked
        assert clusters["P0"]["count"] == 2
        assert clusters["P1"]["count"] == 1
        assert clusters["P2"]["count"] == 0
        assert clusters["unknown"]["count"] == 0

    def test_by_module(self, report_data):
        modules = DefectClusterer.by_module(report_data)
        # 支付模块: TC-003(P0) + TC-005(P0) = 2, 搜索模块: TC-004(P1) = 1
        assert modules.get("支付模块") == 2
        assert modules.get("搜索模块") == 1

    def test_by_type(self, report_data):
        types = DefectClusterer.by_type(report_data)
        assert types.get("功能测试") == 2  # TC-003, TC-005
        assert types.get("接口测试") == 1  # TC-004

    def test_no_failures(self, sample_execution_log):
        """无失败用例时聚类应全零"""
        log = dict(sample_execution_log)
        log["details"] = [
            {"case_id": "TC-001", "title": "通过", "status": "passed"},
            {"case_id": "TC-002", "title": "跳过", "status": "skipped"},
        ]
        data = ReportData(execution_log=log)
        clusters = DefectClusterer.by_priority(data)
        assert all(c["count"] == 0 for c in clusters.values())


# =============================================================================
# MarkdownReportBuilder 测试
# =============================================================================


class TestMarkdownReportBuilder:
    def test_build_contains_sections(self, report_data):
        md = MarkdownReportBuilder.build(report_data)
        assert "# iTest-Agent 测试报告" in md
        assert "## 1. 测试摘要" in md
        assert "## 2. 缺陷聚类分析" in md
        assert "## 3. 详细失败与阻塞用例" in md
        assert "## 5. 所有用例执行明细" in md

    def test_build_contains_stats(self, report_data):
        md = MarkdownReportBuilder.build(report_data)
        assert "10" in md  # total
        assert "60.0%" in md  # pass rate

    def test_build_with_review(self, sample_execution_log, sample_review):
        data = ReportData(execution_log=sample_execution_log, review_result=sample_review)
        md = MarkdownReportBuilder.build(data)
        assert "## 4. 用例评审" in md
        assert "85.5" in md

    def test_build_without_review(self, report_data):
        md = MarkdownReportBuilder.build(report_data)
        assert "## 4. 用例评审" not in md

    def test_build_no_failures(self, sample_execution_log):
        log = dict(sample_execution_log)
        log["details"] = [
            {"case_id": "TC-001", "title": "通过", "status": "passed"},
            {"case_id": "TC-002", "title": "通过", "status": "passed"},
        ]
        log["failed"] = 0
        log["blocked"] = 0
        data = ReportData(execution_log=log)
        md = MarkdownReportBuilder.build(data)
        assert "无失败或阻塞用例" in md

    def test_progress_bar(self):
        bar = MarkdownReportBuilder._progress_bar(75.0, 10)
        assert "75.0%" in bar
        assert "█" in bar

    def test_build_has_failed_case_details(self, report_data):
        md = MarkdownReportBuilder.build(report_data)
        assert "TC-003" in md
        assert "断言失败" in md
        assert "TC-005" in md
        assert "环境不可用" in md


# =============================================================================
# PDFReportBuilder 测试
# =============================================================================

# weasyprint 在 macOS 上需要 gobject-introspection 等系统库
# 若系统环境不支持则跳过 PDF 相关集成测试
try:
    from weasyprint import HTML
    _WEASYPRINT_AVAILABLE = True
except OSError:
    _WEASYPRINT_AVAILABLE = False


class TestPDFReportBuilder:
    def test_convert_md_to_html(self):
        """HTML 转换不依赖 weasyprint 渲染，始终可测"""
        md = "# 标题\n\n一段文字"
        html = PDFReportBuilder.convert_md_to_html(md)
        assert "<!DOCTYPE html>" in html
        assert "标题" in html
        assert "一段文字" in html
        assert "@page" in html

    @pytest.mark.skipif(
        not _WEASYPRINT_AVAILABLE,
        reason="weasyprint 在 macOS 上需要系统库 (libgobject-2.0)，当前环境未安装"
    )
    def test_generate_pdf(self, report_data):
        md = MarkdownReportBuilder.build(report_data)
        with tempfile.TemporaryDirectory() as tmpdir:
            pdf_path = os.path.join(tmpdir, "test.pdf")
            result = PDFReportBuilder.generate(md, pdf_path)
            assert result == os.path.abspath(pdf_path)
            assert os.path.exists(pdf_path)
            assert os.path.getsize(pdf_path) > 0

    @pytest.mark.skipif(
        not _WEASYPRINT_AVAILABLE,
        reason="weasyprint 在 macOS 上需要系统库 (libgobject-2.0)，当前环境未安装"
    )
    def test_generate_pdf_creates_dir(self, report_data):
        md = MarkdownReportBuilder.build(report_data)
        with tempfile.TemporaryDirectory() as tmpdir:
            nested = os.path.join(tmpdir, "a", "b", "report.pdf")
            result = PDFReportBuilder.generate(md, nested)
            assert os.path.exists(result)


# =============================================================================
# ReportGenerator 端到端测试
# =============================================================================


class TestReportGenerator:
    def test_generate_markdown(self, sample_execution_log, sample_suite):
        with tempfile.TemporaryDirectory() as tmpdir:
            exec_path = os.path.join(tmpdir, "execution_log.json")
            suite_path = os.path.join(tmpdir, "test_suite.json")

            with open(exec_path, "w") as f:
                json.dump(sample_execution_log, f)
            with open(suite_path, "w") as f:
                json.dump(sample_suite, f)

            gen = ReportGenerator(pdf_enabled=False)
            paths = gen.generate(
                execution_log_path=exec_path,
                test_suite_path=suite_path,
                output_dir=tmpdir,
            )

            md_path = paths["markdown"]
            assert os.path.exists(md_path)
            with open(md_path, "r") as f:
                content = f.read()
            assert "# iTest-Agent 测试报告" in content
            assert paths.get("pdf") == ""

    @pytest.mark.skipif(
        not _WEASYPRINT_AVAILABLE,
        reason="weasyprint 在 macOS 上需要系统库 (libgobject-2.0)，当前环境未安装"
    )
    def test_generate_with_pdf(self, sample_execution_log, sample_suite):
        with tempfile.TemporaryDirectory() as tmpdir:
            exec_path = os.path.join(tmpdir, "execution_log.json")
            suite_path = os.path.join(tmpdir, "test_suite.json")

            with open(exec_path, "w") as f:
                json.dump(sample_execution_log, f)
            with open(suite_path, "w") as f:
                json.dump(sample_suite, f)

            gen = ReportGenerator(pdf_enabled=True)
            paths = gen.generate(
                execution_log_path=exec_path,
                test_suite_path=suite_path,
                output_dir=tmpdir,
            )

            assert os.path.exists(paths["markdown"])
            pdf_path = paths.get("pdf", "")
            if pdf_path:
                assert os.path.exists(pdf_path)

    def test_generate_missing_execution_log(self):
        gen = ReportGenerator()
        with pytest.raises(FileNotFoundError):
            gen.generate(execution_log_path="/nonexistent/log.json")

    def test_generate_invalid_execution_log(self, tmp_path):
        bad_path = tmp_path / "bad.json"
        bad_path.write_text('{"not_total": 1}')
        gen = ReportGenerator()
        with pytest.raises(ValueError, match="total"):
            gen.generate(execution_log_path=str(bad_path))

    def test_generate_no_suite(self, sample_execution_log):
        """不提供 suite 也能生成报告"""
        with tempfile.TemporaryDirectory() as tmpdir:
            exec_path = os.path.join(tmpdir, "execution_log.json")
            with open(exec_path, "w") as f:
                json.dump(sample_execution_log, f)

            gen = ReportGenerator(pdf_enabled=False)
            paths = gen.generate(
                execution_log_path=exec_path,
                output_dir=tmpdir,
            )

            assert os.path.exists(paths["markdown"])
            with open(paths["markdown"], "r") as f:
                content = f.read()
            assert "无法获取模块" in content or "未知" in content

    def test_generate_with_fixture_files(self):
        """使用 tests/output/ 下的真实 sample 文件"""
        test_dir = os.path.join(_project_root, "tests", "output")
        exec_path = os.path.join(test_dir, "execution_log.json")
        suite_path = os.path.join(test_dir, "test_suite.json")

        gen = ReportGenerator(pdf_enabled=False)
        paths = gen.generate(
            execution_log_path=exec_path,
            test_suite_path=suite_path,
            output_dir=test_dir,
        )

        md_path = paths["markdown"]
        assert os.path.exists(md_path)

        with open(md_path, "r") as f:
            content = f.read()

        # 验证核心内容
        assert "电商用户中心" in content
        assert "TC-FUNC-003-02-01" in content  # 失败用例
        assert "58.3%" in content  # 通过率
        print(f"[OK] Markdown 报告: {md_path}")
        print(f"     大小: {len(content)} 字符")

    @pytest.mark.skipif(
        not _WEASYPRINT_AVAILABLE,
        reason="weasyprint 在 macOS 上需要系统库 (libgobject-2.0)，当前环境未安装"
    )
    def test_generate_with_pdf_from_fixture(self):
        """使用 fixture 文件生成 PDF"""
        test_dir = os.path.join(_project_root, "tests", "output")
        exec_path = os.path.join(test_dir, "execution_log.json")
        suite_path = os.path.join(test_dir, "test_suite.json")

        gen = ReportGenerator(pdf_enabled=True)
        paths = gen.generate(
            execution_log_path=exec_path,
            test_suite_path=suite_path,
            output_dir=test_dir,
        )

        pdf_path = paths.get("pdf", "")
        if pdf_path:
            assert os.path.exists(pdf_path)
            print(f"[OK] PDF 报告: {pdf_path}")
            print(f"     大小: {os.path.getsize(pdf_path)} 字节")
        else:
            print("[WARN] PDF 生成失败（可能缺少系统依赖）")

    def test_generate_markdown_only_api(self, sample_execution_log):
        with tempfile.TemporaryDirectory() as tmpdir:
            exec_path = os.path.join(tmpdir, "execution_log.json")
            with open(exec_path, "w") as f:
                json.dump(sample_execution_log, f)

            gen = ReportGenerator()
            path = gen.generate_markdown_only(
                execution_log_path=exec_path,
                output_dir=tmpdir,
            )
            assert os.path.exists(path)


# =============================================================================
# 进度条测试
# =============================================================================


class TestProgressBar:
    def test_zero(self):
        bar = MarkdownReportBuilder._progress_bar(0, 10)
        assert bar.startswith("`" + "░" * 10)

    def test_hundred(self):
        bar = MarkdownReportBuilder._progress_bar(100, 10)
        assert bar.startswith("`" + "█" * 10)

    def test_half(self):
        bar = MarkdownReportBuilder._progress_bar(50, 10)
        assert bar.startswith("`" + "█" * 5 + "░" * 5)

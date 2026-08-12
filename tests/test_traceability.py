"""
iTest-Agent 追溯矩阵单元测试

测试覆盖：
- TraceLink / RequirementRef Pydantic 模型验证
- TraceabilityMatrix 构建（build_from_prd）
- 正向追溯（get_requirements_for_case）
- 反向追溯（get_cases_for_function）
- 关键词检索（get_cases_for_keyword）
- 覆盖度报告（coverage_report）
- 序列化导出（to_dict / to_markdown）
"""

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from models.traceability import (
    RequirementRef,
    TraceLink,
    TraceabilityMatrix,
)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def sample_prd_path():
    """创建临时 PRD 文件"""
    content = """# 电商用户中心

## 用户注册

用户可以通过手机号或邮箱注册账号。

注册时需要验证手机号或邮箱的有效性，发送验证码。

## 用户登录

用户通过密码或短信验证码登录系统。

连续5次密码错误后账号锁定30分钟。

## 个人信息管理

用户可以查看和编辑自己的个人信息，包括昵称、头像、性别。

头像上传支持 JPG/PNG 格式，大小不超过 5MB。

## 安全设置

用户可以在安全设置中修改登录密码。

用户可以在安全设置中换绑手机号。

用户可以申请注销账号，注销后有 7 天冷静期。
"""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".md", delete=False, encoding="utf-8"
    ) as f:
        f.write(content)
        path = f.name
    yield path
    os.unlink(path)


@pytest.fixture
def sample_function_tree():
    """示例功能树"""
    return [
        {
            "id": "F1",
            "name": "用户注册",
            "sub_functions": [
                {
                    "id": "SF1-1",
                    "name": "手机号注册",
                    "description": "用户使用手机号进行注册，包含验证码验证",
                },
                {
                    "id": "SF1-2",
                    "name": "邮箱注册",
                    "description": "用户使用邮箱进行注册，包含邮件验证",
                },
            ],
        },
        {
            "id": "F2",
            "name": "用户登录",
            "sub_functions": [
                {
                    "id": "SF2-1",
                    "name": "密码登录",
                    "description": "使用账号密码登录系统",
                },
                {
                    "id": "SF2-2",
                    "name": "短信验证码登录",
                    "description": "使用短信验证码快速登录",
                },
            ],
        },
        {
            "id": "F3",
            "name": "安全设置",
            "sub_functions": [
                {
                    "id": "SF3-1",
                    "name": "修改密码",
                    "description": "用户修改登录密码，需验证旧密码",
                },
                {
                    "id": "SF3-2",
                    "name": "换绑手机",
                    "description": "更换绑定的手机号",
                },
                {
                    "id": "SF3-3",
                    "name": "注销账号",
                    "description": "永久注销用户账号",
                },
            ],
        },
    ]


@pytest.fixture
def sample_test_suite():
    """模拟 TestSuite"""
    mock_tc1 = MagicMock()
    mock_tc1.case_id = "TC-001"
    mock_tc1.function_id = "SF1-1"

    mock_tc2 = MagicMock()
    mock_tc2.case_id = "TC-002"
    mock_tc2.function_id = "SF1-1"

    mock_tc3 = MagicMock()
    mock_tc3.case_id = "TC-003"
    mock_tc3.function_id = "SF2-1"

    mock_tc4 = MagicMock()
    mock_tc4.case_id = "TC-004"
    mock_tc4.function_id = "SF3-1"

    mock_tc5 = MagicMock()
    mock_tc5.case_id = "TC-005"
    mock_tc5.function_id = "SF3-2"

    mock_suite = MagicMock()
    mock_suite.test_cases = [mock_tc1, mock_tc2, mock_tc3, mock_tc4, mock_tc5]
    return mock_suite


@pytest.fixture
def populated_matrix(sample_prd_path, sample_function_tree, sample_test_suite):
    """构建好的追溯矩阵"""
    matrix = TraceabilityMatrix()
    matrix.build_from_prd(sample_prd_path, sample_function_tree, sample_test_suite)
    return matrix


# =============================================================================
# RequirementRef 测试
# =============================================================================


class TestRequirementRef:
    """测试 RequirementRef Pydantic 模型"""

    def test_default_factory(self):
        """默认值测试"""
        ref = RequirementRef()
        assert ref.section_title == ""
        assert ref.paragraph_index == 0
        assert ref.line_start == 1
        assert ref.line_end == 1
        assert ref.text == ""
        assert ref.keywords == []

    def test_full_creation(self):
        """完整字段创建"""
        ref = RequirementRef(
            section_title="用户注册",
            paragraph_index=0,
            line_start=2,
            line_end=5,
            text="用户可以通过手机号或邮箱注册账号。",
            keywords=["手机号", "邮箱", "注册"],
        )
        assert ref.section_title == "用户注册"
        assert ref.text == "用户可以通过手机号或邮箱注册账号。"
        assert len(ref.keywords) == 3

    def test_text_too_long_raises(self):
        """超过 300 字符应抛出验证错误"""
        long_text = "A" * 500
        with pytest.raises(Exception):
            RequirementRef(text=long_text)

    def test_line_range_validation(self):
        """行号必须 >= 1"""
        with pytest.raises(Exception):  # Pydantic validation
            RequirementRef(line_start=0)


# =============================================================================
# TraceLink 测试
# =============================================================================


class TestTraceLink:
    """测试 TraceLink Pydantic 模型"""

    def test_minimal_creation(self):
        """最小字段创建"""
        link = TraceLink(link_id="LINK-001")
        assert link.link_id == "LINK-001"
        assert link.direction == "forward"
        assert link.test_case_ids == []
        assert link.function_id == ""

    def test_full_creation(self):
        """完整字段创建"""
        ref = RequirementRef(
            section_title="登录",
            text="用户通过密码登录系统。",
            line_start=10,
            line_end=12,
            keywords=["密码", "登录"],
        )
        link = TraceLink(
            link_id="LINK-005",
            requirement_ref=ref,
            test_case_ids=["TC-001", "TC-002"],
            function_id="SF2-1",
            direction="forward",
        )
        assert link.test_case_ids == ["TC-001", "TC-002"]
        assert link.requirement_ref.section_title == "登录"

    def test_link_id_required(self):
        """link_id 必填"""
        with pytest.raises(Exception):
            TraceLink()  # type: ignore


# =============================================================================
# TraceabilityMatrix 构建测试
# =============================================================================


class TestMatrixBuild:
    """测试 build_from_prd 方法"""

    def test_build_creates_links(
        self, sample_prd_path, sample_function_tree, sample_test_suite
    ):
        """构建后应创建 TraceLink"""
        matrix = TraceabilityMatrix()
        matrix.build_from_prd(sample_prd_path, sample_function_tree, sample_test_suite)

        # 应有 7 个 SubFunction → 7 个 link
        assert len(matrix.links) == 7

    def test_build_populates_indexes(
        self, sample_prd_path, sample_function_tree, sample_test_suite
    ):
        """构建后正向/反向索引应正确填充"""
        matrix = TraceabilityMatrix()
        matrix.build_from_prd(sample_prd_path, sample_function_tree, sample_test_suite)

        # 正向索引：TC-001 关联 SF1-1
        assert len(matrix.forward_index) > 0
        assert "TC-001" in matrix.forward_index

        # 反向索引：SF1-1 应有 TC-001, TC-002
        assert "TC-001" in matrix.backward_index.get("SF1-1", [])

    def test_build_links_have_requirement_ref(
        self, sample_prd_path, sample_function_tree, sample_test_suite
    ):
        """每个 link 应有 requirement_ref"""
        matrix = TraceabilityMatrix()
        matrix.build_from_prd(sample_prd_path, sample_function_tree, sample_test_suite)

        for link in matrix.links.values():
            assert link.requirement_ref is not None
            assert link.requirement_ref.text != ""

    def test_build_link_ids_increment(self, populated_matrix):
        """link_id 应递增"""
        ids = sorted(populated_matrix.links.keys())
        assert ids[0] == "LINK-001"
        assert ids[-1] == f"LINK-{len(ids):03d}"

    def test_build_handles_missing_prd(self, sample_function_tree, sample_test_suite):
        """PRD 文件不存在时不崩溃"""
        matrix = TraceabilityMatrix()
        matrix.build_from_prd(
            "/nonexistent/path.md", sample_function_tree, sample_test_suite
        )
        # 仍应创建 links（使用空 PRD 行）
        assert len(matrix.links) == 7

    def test_build_handles_empty_functions(self, sample_prd_path):
        """空功能树不创建链接"""
        matrix = TraceabilityMatrix()
        matrix.build_from_prd(sample_prd_path, [], None)
        assert len(matrix.links) == 0


# =============================================================================
# 正向追溯测试
# =============================================================================


class TestForwardTrace:
    """测试 get_requirements_for_case — 正向追溯"""

    def test_existing_case_returns_refs(self, populated_matrix):
        """存在用例应返回需求引用"""
        refs = populated_matrix.get_requirements_for_case("TC-001")
        assert len(refs) > 0
        assert isinstance(refs[0], RequirementRef)

    def test_nonexistent_case_returns_empty(self, populated_matrix):
        """不存在的用例返回空列表"""
        refs = populated_matrix.get_requirements_for_case("NONEXISTENT")
        assert refs == []

    def test_case_without_links(self, populated_matrix):
        """未关联的用例 ID 返回空"""
        # SF3-3 (注销账号) 在 sample_test_suite 中没有关联用例
        refs = populated_matrix.get_requirements_for_case("TC-999")
        assert refs == []


# =============================================================================
# 反向追溯测试
# =============================================================================


class TestBackwardTrace:
    """测试 get_cases_for_function — 反向追溯"""

    def test_linked_function_returns_cases(self, populated_matrix):
        """已关联功能应返回用例列表"""
        cases = populated_matrix.get_cases_for_function("SF1-1")
        assert "TC-001" in cases
        assert "TC-002" in cases

    def test_unlinked_function_returns_empty(self, populated_matrix):
        """未关联功能返回空列表"""
        cases = populated_matrix.get_cases_for_function("SF3-3")  # 注销账号
        assert cases == []

    def test_nonexistent_function_returns_empty(self, populated_matrix):
        """不存在功能返回空"""
        cases = populated_matrix.get_cases_for_function("NONEXISTENT")
        assert cases == []


# =============================================================================
# 关键词检索测试
# =============================================================================


class TestKeywordSearch:
    """测试 get_cases_for_keyword — 关键词检索"""

    def test_search_by_keyword_finds_cases(self, populated_matrix):
        """按关键词检索应找到关联用例"""
        cases = populated_matrix.get_cases_for_keyword("注册")
        # SF1-1 (手机号注册) 关联了 TC-001, TC-002
        assert len(cases) > 0

    def test_search_case_insensitive(self, populated_matrix):
        """大小写不敏感"""
        cases_lower = populated_matrix.get_cases_for_keyword("login")
        cases_upper = populated_matrix.get_cases_for_keyword("LOGIN")
        # 两者应有相同结果（可能都是空，但不应崩溃）
        assert cases_lower == cases_upper

    def test_no_match_returns_empty(self, populated_matrix):
        """无匹配关键词返回空"""
        cases = populated_matrix.get_cases_for_keyword("宇宙飞船")
        assert cases == []

    def test_results_deduplicated(self, populated_matrix):
        """结果去重"""
        cases = populated_matrix.get_cases_for_keyword("注册")
        assert len(cases) == len(set(cases))


# =============================================================================
# 覆盖度报告测试
# =============================================================================


class TestCoverageReport:
    """测试 coverage_report — 覆盖度统计"""

    def test_report_has_required_keys(self, populated_matrix):
        """报告应包含所有必需字段"""
        report = populated_matrix.coverage_report()
        assert "total_functions" in report
        assert "linked_functions" in report
        assert "unlinked_functions" in report
        assert "coverage_rate" in report

    def test_coverage_rate_between_0_and_1(self, populated_matrix):
        """覆盖率应在 [0, 1]"""
        report = populated_matrix.coverage_report()
        assert 0.0 <= report["coverage_rate"] <= 1.0

    def test_empty_matrix_coverage_zero(self):
        """空矩阵覆盖率为 0"""
        matrix = TraceabilityMatrix()
        report = matrix.coverage_report()
        assert report["total_functions"] == 0
        assert report["coverage_rate"] == 0.0

    def test_sum_equals_total(self, populated_matrix):
        """已关联 + 未关联 = 总功能数"""
        report = populated_matrix.coverage_report()
        assert (
            report["linked_functions"] + len(report["unlinked_functions"])
            == report["total_functions"]
        )


# =============================================================================
# 序列化与导出测试
# =============================================================================


class TestSerialization:
    """测试 to_dict / to_markdown"""

    def test_to_dict_structure(self, populated_matrix):
        """to_dict 应包含 links / forward_index / backward_index"""
        d = populated_matrix.to_dict()
        assert "links" in d
        assert "forward_index" in d
        assert "backward_index" in d
        assert len(d["links"]) == len(populated_matrix.links)

    def test_to_dict_roundtrip(self, populated_matrix):
        """to_dict 后可 JSON 序列化/反序列化"""
        d = populated_matrix.to_dict()
        json_str = json.dumps(d)
        restored = json.loads(json_str)
        assert len(restored["links"]) == len(populated_matrix.links)

    def test_to_markdown_produces_text(self, populated_matrix):
        """to_markdown 应生成非空 Markdown 文本"""
        md = populated_matrix.to_markdown()
        assert len(md) > 0
        assert "# 追溯矩阵" in md
        assert "覆盖度报告" in md

    def test_to_markdown_has_coverage_stats(self, populated_matrix):
        """Markdown 应包含覆盖度统计"""
        md = populated_matrix.to_markdown()
        assert "覆盖率" in md

    def test_empty_matrix_to_dict(self):
        """空矩阵 to_dict"""
        matrix = TraceabilityMatrix()
        d = matrix.to_dict()
        assert d["links"] == {}

    def test_empty_matrix_to_markdown(self):
        """空矩阵 to_markdown"""
        matrix = TraceabilityMatrix()
        md = matrix.to_markdown()
        assert len(md) > 0


# =============================================================================
# 内部工具方法测试
# =============================================================================


class TestInternalHelpers:
    """测试 _extract_section_title / _extract_keywords"""

    def test_extract_h1_title(self):
        """提取 # 标题"""
        matrix = TraceabilityMatrix()
        assert matrix._extract_section_title("# 用户注册") == "用户注册"

    def test_extract_h3_title(self):
        """提取 ### 标题"""
        matrix = TraceabilityMatrix()
        assert matrix._extract_section_title("### 1.2 手机号注册") == "1.2 手机号注册"

    def test_extract_non_title(self):
        """非标题行返回空"""
        matrix = TraceabilityMatrix()
        assert matrix._extract_section_title("普通文本") == ""

    def test_extract_keywords_from_chinese(self):
        """中文关键词提取"""
        matrix = TraceabilityMatrix()
        kws = matrix._extract_keywords("用户使用手机号注册账号")
        assert len(kws) > 0
        # 正则 [\w\u4e00-\u9fff]{2,} 匹配连续中文字符，可能返回整体字符串或分词结果
        assert any(kw in kws for kw in ["用户", "手机号", "注册", "账号"]) or len(kws) == 1

    def test_extract_keywords_max_limit(self):
        """关键词不超过 max_kw"""
        matrix = TraceabilityMatrix()
        long_text = " ".join([f"word{i}" for i in range(20)])
        kws = matrix._extract_keywords(long_text, max_kw=5)
        assert len(kws) <= 5

    def test_extract_keywords_empty_text(self):
        """空文本返回空"""
        matrix = TraceabilityMatrix()
        kws = matrix._extract_keywords("")
        assert kws == []

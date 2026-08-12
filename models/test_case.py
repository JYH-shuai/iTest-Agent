"""
iTest-Agent 标准化测试用例数据模型

定义 TestCase / TestStep / TestSuite 的 Pydantic 模型，
并提供从需求分析结果（RequirementAnalysisResult）到测试用例骨架的映射函数。

设计原则：
- 每条用例可独立执行，前置条件明确、测试数据具体
- 双向可追溯：用例 ↔ 功能点 ↔ 需求原文
- 支持 Excel / JSON / Markdown 多格式导出
"""

import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator


# =============================================================================
# 常量
# =============================================================================

VALID_CASE_TYPES = ("功能测试", "接口测试", "性能测试", "安全测试", "兼容性测试")
VALID_PRIORITIES = ("P0", "P1", "P2")
VALID_STATUSES = ("待执行", "已通过", "已失败", "已阻塞", "已跳过")


# =============================================================================
# 数据模型
# =============================================================================


class TestStep(BaseModel):
    """单个测试步骤"""

    step: int = Field(..., ge=1, description="步骤序号，从 1 开始")
    action: str = Field(..., min_length=1, description="动词开头的操作描述")
    expected: str = Field(..., min_length=1, description="精确可验证的预期结果")


class Traceability(BaseModel):
    """
    追溯信息：将用例关联到需求原文

    requirement_source / requirement_section / requirement_text 保留作为摘要字段，
    保持向后兼容。requirement_refs 提供精确到行号的上下文引用列表。
    """

    requirement_source: str = Field(
        default="", description="需求文档名称或标识"
    )
    requirement_section: str = Field(
        default="", description="需求文档中相关章节/段落"
    )
    requirement_text: str = Field(
        default="", description="需求原文引用（关键句）"
    )
    requirement_refs: List[dict] = Field(
        default_factory=list,
        description=(
            "精确需求引用列表，每个 dict 包含 section_title / paragraph_index / "
            "line_start / line_end / text / keywords 等字段，"
            "用于双向追溯矩阵的构建"
        ),
    )


class TestCase(BaseModel):
    """
    标准化测试用例模型

    每条用例对应需求分析中的一个二级子功能（SubFunction），
    支持一对多关系（一个子功能可生成多条用例覆盖不同场景）。
    """

    case_id: str = Field(..., min_length=1, description="用例唯一 ID，如 TC-FUNC-001-01-01")
    title: str = Field(
        ..., min_length=1, max_length=200, description="用例标题，动词开头，简洁明确"
    )
    function_id: str = Field(..., min_length=1, description="关联的子功能 ID（来自 FunctionNode）")
    function_name: str = Field(default="", description="关联的功能名称（便于阅读）")
    type: str = Field(
        default="功能测试",
        description="用例类型：功能测试|接口测试|性能测试|安全测试|兼容性测试",
    )
    priority: str = Field(default="P1", description="优先级：P0|P1|P2")
    precondition: str = Field(
        default="无", description="前置条件，须具体可复现"
    )
    test_data: Dict[str, Any] = Field(
        default_factory=dict, description="测试数据键值对"
    )
    steps: List[TestStep] = Field(
        default_factory=list, description="测试步骤列表"
    )
    tags: List[str] = Field(
        default_factory=list, description="标签，用于分类筛选"
    )
    design_method: str = Field(
        default="", description="用例设计方法，如等价类划分、边界值分析"
    )
    cleanup: str = Field(
        default="", description="测试后清理步骤"
    )
    traceability: Traceability = Field(
        default_factory=Traceability, description="需求追溯信息"
    )
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        description="用例创建时间（ISO 8601 UTC）",
    )
    status: str = Field(
        default="待执行",
        description="执行状态：待执行|已通过|已失败|已阻塞|已跳过",
    )

    @field_validator("type")
    @classmethod
    def validate_type(cls, v: str) -> str:
        if v not in VALID_CASE_TYPES:
            raise ValueError(f"无效用例类型 '{v}'，有效值: {VALID_CASE_TYPES}")
        return v

    @field_validator("priority")
    @classmethod
    def validate_priority(cls, v: str) -> str:
        if v not in VALID_PRIORITIES:
            raise ValueError(f"无效优先级 '{v}'，有效值: {VALID_PRIORITIES}")
        return v

    @field_validator("status")
    @classmethod
    def validate_status(cls, v: str) -> str:
        if v not in VALID_STATUSES:
            raise ValueError(f"无效状态 '{v}'，有效值: {VALID_STATUSES}")
        return v

    # ---- 导出辅助方法 ----

    def to_flat_row(self) -> Dict[str, Any]:
        """
        将用例展平为一行字典，用于导出为表格（Excel/CSV）

        将 steps 和 test_data 序列化为易读的字符串格式。
        """
        steps_text = "\n".join(
            f"步骤{s.step}: {s.action} -> 预期: {s.expected}"
            for s in self.steps
        )
        test_data_text = (
            json.dumps(self.test_data, ensure_ascii=False)
            if self.test_data
            else ""
        )

        return {
            "用例ID": self.case_id,
            "用例标题": self.title,
            "关联功能ID": self.function_id,
            "功能名称": self.function_name,
            "用例类型": self.type,
            "优先级": self.priority,
            "前置条件": self.precondition,
            "测试数据": test_data_text,
            "测试步骤": steps_text,
            "标签": "、".join(self.tags),
            "设计方法": self.design_method,
            "清理步骤": self.cleanup,
            "需求来源": self.traceability.requirement_source,
            "需求章节": self.traceability.requirement_section,
            "需求原文引用": json.dumps(
                self.traceability.requirement_refs, ensure_ascii=False
            ) if self.traceability.requirement_refs else "",
            "状态": self.status,
            "创建时间": self.created_at,
        }


class TestSuite(BaseModel):
    """
    测试用例集

    对应一份需求文档的完整测试用例集合，包含元信息和统计。
    """

    suite_name: str = Field(..., description="测试套件名称")
    product_name: str = Field(default="", description="产品名称")
    module_name: str = Field(default="", description="模块名称")
    total_cases: int = Field(default=0)
    p0_count: int = Field(default=0)
    p1_count: int = Field(default=0)
    p2_count: int = Field(default=0)
    test_cases: List[TestCase] = Field(default_factory=list)
    generated_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    )

    # ---- 统计刷新 ----

    def refresh_stats(self) -> None:
        """根据 test_cases 刷新统计字段"""
        self.total_cases = len(self.test_cases)
        self.p0_count = sum(1 for tc in self.test_cases if tc.priority == "P0")
        self.p1_count = sum(1 for tc in self.test_cases if tc.priority == "P1")
        self.p2_count = sum(1 for tc in self.test_cases if tc.priority == "P2")


# =============================================================================
# 功能点到用例骨架映射
# =============================================================================


def _infer_test_type(function_name: str, description: str) -> str:
    """
    根据功能名称和描述推断默认用例类型

    规则：
    - 含"性能/并发/压力/负载" → 性能测试
    - 含"安全/权限/认证/加密" → 安全测试
    - 含"API/接口/调用" → 接口测试
    - 默认 → 功能测试
    """
    combined = f"{function_name} {description}".lower()
    if any(kw in combined for kw in ("性能", "并发", "压力", "负载", "吞吐")):
        return "性能测试"
    if any(kw in combined for kw in ("安全", "权限", "认证", "加密", "注入", "csrf", "xss")):
        return "安全测试"
    if any(kw in combined for kw in ("api", "接口", "调用", "http", "rest")):
        return "接口测试"
    if any(kw in combined for kw in ("兼容", "浏览器", "平台", "设备")):
        return "兼容性测试"
    return "功能测试"


def _infer_design_method(test_suggestions: List) -> str:
    """从测试建议中提取设计方法名"""
    if test_suggestions:
        first = test_suggestions[0]
        method = getattr(first, "method", "") if hasattr(first, "method") else first.get("method", "")
        if method:
            return method
    return "场景法"


def map_function_to_testcase(
    func_id: str,
    func_name: str,
    func_desc: str,
    func_priority: str,
    acceptance_criteria: List[str],
    test_suggestions: List,
    case_index: int = 1,
    prefix: str = "TC",
    requirement_source: str = "",
    requirement_section: str = "",
    requirement_text: str = "",
) -> TestCase:
    """
    从一个二级子功能生成单个测试用例骨架

    将 FunctionNode / SubFunction 的信息映射为 TestCase 的基础字段。
    实际用例填充（test_data / steps）可由 LLM 或手工补充。

    Args:
        func_id: 功能 ID（如 FUNC-001-01）
        func_name: 功能名称
        func_desc: 功能描述
        func_priority: 优先级
        acceptance_criteria: 验收条件列表
        test_suggestions: 测试建议列表（dict 或 TestSuggestion 对象）
        case_index: 用例序号
        prefix: 用例 ID 前缀
        requirement_source: 需求文档标识
        requirement_section: 需求章节
        requirement_text: 需求原文引用

    Returns:
        TestCase: 包含骨架字段的标准用例对象
    """
    case_id = f"{prefix}-{func_id}-{case_index:02d}"

    # 用验收条件构建初始步骤
    steps = [
        TestStep(
            step=i + 1,
            action=ac,
            expected=ac,
        )
        for i, ac in enumerate(acceptance_criteria)
    ]

    design_method = _infer_design_method(test_suggestions)
    case_type = _infer_test_type(func_name, func_desc)

    return TestCase(
        case_id=case_id,
        title=f"{func_name} - Happy Path 验证",
        function_id=func_id,
        function_name=func_name,
        type=case_type,
        priority=func_priority,
        precondition="参照功能描述中的前置依赖",
        test_data={},
        steps=steps,
        tags=["冒烟测试", "核心流程"] if func_priority == "P0" else [],
        design_method=design_method,
        cleanup="",
        traceability=Traceability(
            requirement_source=requirement_source,
            requirement_section=requirement_section,
            requirement_text=requirement_text,
        ),
    )


def map_analysis_to_testsuite(
    analysis_result,
    requirement_source: str = "",
    requirement_path: str = "",
) -> TestSuite:
    """
    从 RequirementAnalysisResult 构建 TestSuite

    遍历 function_tree，为每个二级子功能生成 1 条骨架用例。

    Args:
        analysis_result: RequirementAnalysisResult 实例
        requirement_source: 需求文档标识（文件名）
        requirement_path: 需求文档路径（用于读取原文 section）

    Returns:
        TestSuite: 包含所有骨架用例的测试套件
    """
    suite = TestSuite(
        suite_name=f"{analysis_result.overview.product_name or '产品'} - {analysis_result.overview.module_name or '模块'} 测试用例集",
        product_name=analysis_result.overview.product_name,
        module_name=analysis_result.overview.module_name,
    )

    # 如果提供了 PRD 路径，读取原文用于追溯
    prd_content = ""
    if requirement_path and os.path.exists(requirement_path):
        try:
            with open(requirement_path, "r", encoding="utf-8") as f:
                prd_content = f.read()
        except Exception:
            pass

    for func in analysis_result.function_tree:
        func_dict = func if isinstance(func, dict) else func.model_dump()

        for sf_raw in func_dict.get("sub_functions", []):
            sf = sf_raw if isinstance(sf_raw, dict) else sf_raw.model_dump() if hasattr(sf_raw, "model_dump") else sf_raw

            sf_id = sf.get("id", "")
            sf_name = sf.get("name", "")
            sf_desc = sf.get("description", "")
            sf_priority = sf.get("priority", "P1")
            ac_list = sf.get("acceptance_criteria", [])
            suggestions = sf.get("test_suggestions", [])

            # 在需求原文中查找相关段落作为追溯
            requirement_section = ""
            requirement_text = ""
            if prd_content and sf_name:
                # 简单匹配：查找包含功能名称的段落
                lines = prd_content.split("\n")
                for line in lines:
                    if sf_name in line:
                        requirement_text = line.strip()[:200]
                        break

            tc = map_function_to_testcase(
                func_id=sf_id,
                func_name=sf_name,
                func_desc=sf_desc,
                func_priority=sf_priority,
                acceptance_criteria=ac_list,
                test_suggestions=suggestions,
                case_index=1,
                requirement_source=requirement_source,
                requirement_section=requirement_section,
                requirement_text=requirement_text,
            )
            suite.test_cases.append(tc)

    suite.refresh_stats()
    return suite

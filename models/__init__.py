"""
iTest-Agent 数据模型 — 标准化测试用例与映射关系

导出：
    TestStep, TestCase, TestSuite — 核心测试用例模型
    TraceabilityMatrix, RequirementRef, TraceLink — 双向追溯矩阵模型
    map_function_to_testcase — 从 FunctionNode 生成 TestCase 骨架
"""

from models.test_case import (
    TestCase,
    TestStep,
    TestSuite,
    map_function_to_testcase,
    map_analysis_to_testsuite,
)
from models.traceability import (
    RequirementRef,
    TraceLink,
    TraceabilityMatrix,
)

__all__ = [
    "TestCase",
    "TestStep",
    "TestSuite",
    "RequirementRef",
    "TraceLink",
    "TraceabilityMatrix",
    "map_function_to_testcase",
    "map_analysis_to_testsuite",
]

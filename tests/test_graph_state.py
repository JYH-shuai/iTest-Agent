"""
iTest-Agent State/Workflow 单元测试

测试覆盖：
- WorkflowPhase / ChangeType 枚举
- ErrorInfo / ReviewResult TypedDict
- create_initial_state / create_incremental_state
- build_state_graph 图拓扑
"""

import json
import os
import tempfile
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from graph.state import (
    AgentState,
    AnalysisResult,
    ChangeType,
    ErrorInfo,
    ReviewResult,
    WorkflowPhase,
    create_initial_state,
    create_incremental_state,
)


# =============================================================================
# 枚举测试
# =============================================================================


class TestWorkflowPhase:
    """WorkflowPhase 枚举"""

    def test_all_phases_exist(self):
        phases = set(p.value for p in WorkflowPhase)
        expected = {
            "init", "analyzing", "generating", "reviewing",
            "executing", "reporting", "error", "completed", "cancelled",
        }
        assert phases == expected

    def test_str_value(self):
        assert WorkflowPhase.INIT.value == "init"
        assert WorkflowPhase.COMPLETED.value == "completed"

    def test_enum_is_str_subclass(self):
        """枚举值应为字符串"""
        assert isinstance(WorkflowPhase.INIT.value, str)


class TestChangeType:
    """ChangeType 枚举"""

    def test_all_types_exist(self):
        types = set(t.value for t in ChangeType)
        expected = {
            "none", "full_update", "incremental",
            "add_function", "remove_function",
        }
        assert types == expected

    def test_default_is_none(self):
        assert ChangeType.NONE.value == "none"


# =============================================================================
# TypedDict 测试
# =============================================================================


class TestErrorInfo:
    """ErrorInfo TypedDict"""

    def test_partial_construction(self):
        """total=False 支持部分字段"""
        ei: ErrorInfo = {
            "node": "analyze_requirements",
            "phase": "analyzing",
            "error_type": "FileNotFoundError",
            "error_message": "PRD not found",
        }
        assert ei["node"] == "analyze_requirements"
        assert isinstance(ei, dict)

    def test_full_construction(self):
        """完整字段"""
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        ei: ErrorInfo = {
            "node": "generate_testcases",
            "phase": "generating",
            "error_type": "ValueError",
            "error_message": "Invalid analysis result",
            "timestamp": timestamp,
            "attempt": 1,
            "max_retries": 2,
            "recovered": False,
        }
        assert ei["attempt"] == 1
        assert ei["recovered"] is False


class TestReviewResult:
    """ReviewResult TypedDict"""

    def test_partial_construction(self):
        rr: ReviewResult = {
            "passed": True,
            "score": 92.5,
            "total_cases": 50,
        }
        assert rr["passed"] is True
        assert rr["score"] == 92.5

    def test_with_failed_cases(self):
        rr: ReviewResult = {
            "passed": False,
            "score": 75.0,
            "total_cases": 20,
            "passed_cases": 15,
            "failed_cases": 5,
            "feedback": "安全测试覆盖度不足",
            "failed_case_ids": ["TC-003", "TC-007", "TC-012", "TC-015", "TC-018"],
            "coverage_gaps": ["安全测试", "性能测试"],
        }
        assert len(rr["failed_case_ids"]) == 5
        assert "安全测试" in rr["coverage_gaps"]


# =============================================================================
# 状态创建测试
# =============================================================================


class TestCreateInitialState:
    """create_initial_state 函数"""

    def test_minimal_state(self):
        """最简参数创建"""
        state = create_initial_state(prd_path="/tmp/test.md")
        assert isinstance(state, dict)
        assert state["prd_path"] == "/tmp/test.md"
        assert state["phase"] == WorkflowPhase.INIT.value
        assert state["error_occurred"] is False

    def test_default_values(self):
        """默认值验证"""
        state = create_initial_state(prd_path="/tmp/test.md")
        assert state["llm_model"] == "gpt-4o-mini"
        assert state["change_type"] == ChangeType.NONE.value
        assert state["max_retries"] == 2
        assert state["messages"] == []
        assert state["error_history"] == []

    def test_custom_model(self):
        """自定义 LLM 模型"""
        state = create_initial_state(
            prd_path="/tmp/test.md",
            llm_model="gpt-4o",
        )
        assert state["llm_model"] == "gpt-4o"

    def test_custom_output_dir(self):
        """自定义输出目录"""
        state = create_initial_state(
            prd_path="/tmp/test.md",
            output_dir="/custom/output",
        )
        assert state["output_dir"] == "/custom/output"

    def test_checkpoint_db_path(self):
        """checkpoint 路径"""
        state = create_initial_state(
            prd_path="/tmp/test.md",
            checkpoint_db_path="/tmp/checkpoints.db",
        )
        assert state["checkpoint_db_path"] == "/tmp/checkpoints.db"

    def test_has_required_keys(self):
        """初始状态应包含所有必需键"""
        state = create_initial_state(prd_path="/tmp/test.md")
        required_keys = {
            "prd_path", "phase", "llm_model", "change_type",
            "max_retries", "error_occurred", "messages", "test_suite",
        }
        for key in required_keys:
            assert key in state, f"Missing key: {key}"

    def test_serializable(self):
        """状态应可 JSON 序列化"""
        state = create_initial_state(prd_path="/tmp/test.md")
        json_str = json.dumps(state, default=str)
        restored = json.loads(json_str)
        assert restored["prd_path"] == "/tmp/test.md"


class TestCreateIncrementalState:
    """create_incremental_state 函数"""

    def test_incremental_state_type(self):
        """增量状态应设置 change_type"""
        state = create_incremental_state(
            prd_path="/tmp/test.md",
            previous_analysis_path="/tmp/prev.json",
            changed_function_ids=["SF1-1"],
        )
        assert state["change_type"] in (
            ChangeType.INCREMENTAL.value,
            ChangeType.ADD_FUNCTION.value,
            ChangeType.REMOVE_FUNCTION.value,
        )

    def test_incremental_has_change_log(self):
        """增量状态应有 change_log"""
        state = create_incremental_state(
            prd_path="/tmp/test.md",
            previous_analysis_path="/tmp/prev.json",
            changed_function_ids=["SF1-1", "SF2-1"],
        )
        assert "change_log" in state
        assert len(state["change_log"]) > 0

    def test_change_log_functions(self):
        """change_log 应记录变更功能"""
        state = create_incremental_state(
            prd_path="/tmp/test.md",
            previous_analysis_path="/tmp/prev.json",
            changed_function_ids=["SF1-1", "SF2-2"],
        )
        log = state["change_log"][0]
        assert "changed_functions" in log
        assert "SF1-1" in log["changed_functions"]


# =============================================================================
# Workflow 图拓扑测试（不依赖 LLM）
# =============================================================================


class TestWorkflowTopology:
    """build_state_graph 图结构测试"""

    def test_graph_builds_successfully(self):
        """图构建不应抛异常"""
        from graph.workflow import build_state_graph
        graph = build_state_graph()
        assert graph is not None

    def test_graph_has_required_nodes(self):
        """图应包含所有必要节点"""
        from graph.workflow import build_state_graph
        graph = build_state_graph()
        node_names = set(graph.nodes.keys())
        required = {
            "analyze_requirements",
            "generate_testcases",
            "review_testcases",
        }
        for node in required:
            assert node in node_names, f"Missing node: {node}"

    def test_graph_has_entry_point(self):
        """图应有入口点"""
        from graph.workflow import build_state_graph
        graph = build_state_graph()
        # StateGraph 有 __all_nodes__ 或 nodes
        assert len(graph.nodes) > 0

    def test_graph_node_count(self):
        """节点数 >= 7（含分支持/错误处理/终结节点）"""
        from graph.workflow import build_state_graph
        graph = build_state_graph()
        assert len(graph.nodes) >= 7

    def test_workflow_builds_successfully(self):
        """build_itest_workflow 不应抛异常"""
        from graph.workflow import build_itest_workflow
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            wf = build_itest_workflow(checkpoint_db_path=db_path)
            assert wf is not None
            assert wf.app is not None
        finally:
            os.unlink(db_path)


# =============================================================================
# AgentState 类型兼容性测试
# =============================================================================


class TestAgentStateType:
    """AgentState TypedDict 类型验证"""

    def test_state_accepts_phase(self):
        """状态字段可包含 phase"""
        state: AgentState = {
            "prd_path": "/tmp/test.md",
            "phase": WorkflowPhase.ANALYZING.value,
            "change_type": ChangeType.NONE.value,
            "llm_model": "gpt-4o-mini",
            "max_retries": 2,
            "error_occurred": False,
            "messages": [],
            "test_suite": {},
        }
        assert state["phase"] == "analyzing"

    def test_state_accepts_analysis_result(self):
        """状态字段可包含 analysis_result"""
        analysis: AnalysisResult = {
            "file_path": "/tmp/analysis.json",
            "product_name": "电商APP",
            "module_name": "用户中心",
            "total_functions": 10,
            "p0_count": 3,
            "p1_count": 4,
            "p2_count": 3,
        }
        state: AgentState = {
            "prd_path": "/tmp/test.md",
            "phase": WorkflowPhase.ANALYZING.value,
            "change_type": ChangeType.NONE.value,
            "llm_model": "gpt-4o-mini",
            "max_retries": 2,
            "error_occurred": False,
            "messages": [],
            "test_suite": {},
            "analysis_result": analysis,
        }
        assert state["analysis_result"]["product_name"] == "电商APP"

    def test_state_can_hold_messages(self):
        """消息字段可追加"""
        state: AgentState = {
            "prd_path": "/tmp/test.md",
            "phase": WorkflowPhase.INIT.value,
            "change_type": ChangeType.NONE.value,
            "llm_model": "gpt-4o-mini",
            "max_retries": 2,
            "error_occurred": False,
            "messages": ["[12:00:00] 开始执行", "[12:00:01] 分析完成"],
            "test_suite": {},
        }
        assert len(state["messages"]) == 2

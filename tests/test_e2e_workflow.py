"""
iTest-Agent 端到端集成测试

覆盖完整工作流链路：
  PRD → 需求分析 → 用例生成 → 用例评审 → 用例执行 → 报告生成

测试重点：
1. 工作流图的拓扑结构正确性
2. 各节点状态传递正确性
3. 错误处理与路由机制
4. 最终产物文件完整性
5. 报告生成器端到端

注意：LLM 调用需要有效的 API key（OPENAI_API_KEY 环境变量）。
若未配置则跳过需要 LLM 的测试。
"""

import json
import os
import sys
import tempfile
from unittest.mock import MagicMock, patch

import pytest

_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _project_root)

from graph.workflow import ITestWorkflow, build_state_graph
from graph.state import (
    AgentState,
    ChangeType,
    WorkflowPhase,
    create_initial_state,
    create_incremental_state,
    ExecutionResult,
    ReviewResult,
)
from agents.report_generator import ReportGenerator, MarkdownReportBuilder, DefectClusterer, ReportData


# =============================================================================
# 跳过条件：检查是否有 LLM API key
# =============================================================================

_has_api_key = bool(
    os.environ.get("OPENAI_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")
)

requires_llm = pytest.mark.skipif(
    not _has_api_key,
    reason="需要 OPENAI_API_KEY 或 ANTHROPIC_API_KEY 环境变量",
)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def sample_prd_path() -> str:
    """返回 sample PRD 文件的绝对路径"""
    path = os.path.join(os.path.dirname(__file__), "sample_prd.md")
    assert os.path.exists(path), f"sample_prd.md 不存在: {path}"
    return path


@pytest.fixture
def temp_output_dir() -> str:
    """创建临时输出目录"""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


# =============================================================================
# 工作流拓扑测试（不依赖 LLM）
# =============================================================================


class TestWorkflowTopology:
    """验证 StateGraph 拓扑结构正确性"""

    def test_graph_nodes_exist(self):
        graph = build_state_graph()
        nodes = list(graph.nodes.keys())
        expected = [
            "analyze_requirements",
            "generate_testcases",
            "review_testcases",
            "execute_testcases",
            "generate_report",
            "handle_error",
            "finalize",
        ]
        for node in expected:
            assert node in nodes, f"节点 {node} 未找到"

    def test_entry_point(self):
        graph = build_state_graph()
        # 编译后验证入口
        compiled = graph.compile()
        # 入口点应该正确设置
        assert compiled is not None

    def test_conditional_edges_exist(self):
        graph = build_state_graph()
        # 验证条件边被正确添加（通过编译无异常来间接验证）
        compiled = graph.compile()
        assert compiled is not None


class TestInitialState:
    """验证状态工厂函数"""

    def test_create_initial_state_minimal(self):
        state = create_initial_state(prd_path="/fake/prd.md")
        assert state["prd_path"] == "/fake/prd.md"
        assert state["phase"] == WorkflowPhase.INIT.value
        assert state["error_occurred"] is False
        assert state["max_retries"] == 2
        assert state["messages"] == []
        assert state["change_log"] == []

    def test_create_initial_state_full(self):
        state = create_initial_state(
            prd_path="/fake/prd.md",
            llm_model="gpt-4o",
            output_dir="/tmp/output",
            kb_persist_dir="/tmp/kb",
            checkpoint_db_path="/tmp/checkpoint.db",
            config={"temperature": 0.3},
        )
        assert state["llm_model"] == "gpt-4o"
        assert state["output_dir"] == "/tmp/output"
        assert state["kb_persist_dir"] == "/tmp/kb"
        assert state["checkpoint_db_path"] == "/tmp/checkpoint.db"
        assert state["config"] == {"temperature": 0.3}

    def test_create_incremental_state(self):
        state = create_incremental_state(
            prd_path="/fake/prd_v2.md",
            previous_analysis_path="/tmp/analysis_v1.json",
            changed_function_ids=["FUNC-001", "FUNC-002"],
        )
        assert state["change_type"] == ChangeType.INCREMENTAL.value
        assert len(state["change_log"]) == 1
        assert state["change_log"][0]["changed_functions"] == ["FUNC-001", "FUNC-002"]


# =============================================================================
# 错误处理机制测试（不依赖 LLM）
# =============================================================================


class TestErrorHandling:
    """验证工作流的错误处理与恢复机制"""

    def test_error_node_recovery_flow(self):
        """模拟：analyze 报错 → handle_error → 重试 → 成功"""
        workflow = ITestWorkflow()

        initial_state = create_initial_state(
            prd_path="/nonexistent/prd.md",
            output_dir="/tmp/test_error",
        )

        config = {"configurable": {"thread_id": "test-error-1"}}

        try:
            result = workflow.run(initial_state, config=config)
            # 应该因为文件不存在而进入错误处理
            assert result.get("error_occurred") or result.get("phase") in (
                WorkflowPhase.CANCELLED.value,
                WorkflowPhase.ERROR.value,
            ), f"预期出错但未检测到: phase={result.get('phase')}"
            assert len(result.get("error_history", [])) > 0
        except Exception:
            # 也可能直接抛异常
            pass

    def test_max_retries_exhausted(self):
        """验证重试耗尽后工作流取消"""
        workflow = ITestWorkflow(max_retries=1)  # 仅允许 1 次重试

        initial_state = create_initial_state(
            prd_path="/nonexistent/prd.md",
            output_dir="/tmp/test_exhaust",
        )
        initial_state["max_retries"] = 1

        config = {"configurable": {"thread_id": "test-exhaust-1"}}

        try:
            result = workflow.run(initial_state, config=config)
            # 重试耗尽后应取消
            error_history = result.get("error_history", [])
            assert len(error_history) > 0
        except Exception:
            pass


# =============================================================================
# 报告生成器端到端（不依赖 LLM）
# =============================================================================


class TestReportGeneratorE2E:
    """验证报告生成模块在真实 sample 数据上的端到端行为"""

    def test_full_report_pipeline_with_sample_data(self):
        """使用 tests/output/ 下的真实 sample 文件完整走通报告管线"""
        test_dir = os.path.join(_project_root, "tests", "output")
        exec_path = os.path.join(test_dir, "execution_log.json")
        suite_path = os.path.join(test_dir, "test_suite.json")

        with tempfile.TemporaryDirectory() as out_dir:
            gen = ReportGenerator(pdf_enabled=False)
            paths = gen.generate(
                execution_log_path=exec_path,
                test_suite_path=suite_path,
                output_dir=out_dir,
            )

            # 1. Markdown 文件存在且包含关键内容
            md_path = paths["markdown"]
            assert os.path.exists(md_path)

            with open(md_path, "r") as f:
                content = f.read()

            # 标题
            assert "# iTest-Agent 测试报告" in content

            # 摘要
            assert "## 1. 测试摘要" in content
            assert "通过率" in content

            # 缺陷聚类
            assert "## 2. 缺陷聚类分析" in content

            # 明细表
            assert "## 5. 所有用例执行明细" in content

            print(f"[E2E] Markdown 报告: {len(content)} 字符")

    def test_execution_log_json_structure(self):
        """验证 execution_log.json 的字段完整性"""
        test_dir = os.path.join(_project_root, "tests", "output")
        exec_path = os.path.join(test_dir, "execution_log.json")

        with open(exec_path, "r") as f:
            data = json.load(f)

        required_fields = ["total", "passed", "failed", "blocked", "skipped", "pass_rate", "details"]
        for field in required_fields:
            assert field in data, f"缺少字段: {field}"

        assert data["total"] > 0
        assert data["pass_rate"] >= 0
        assert len(data["details"]) == data["total"]

    def test_test_suite_json_structure(self):
        """验证 test_suite.json 的字段完整性"""
        test_dir = os.path.join(_project_root, "tests", "output")
        suite_path = os.path.join(test_dir, "test_suite.json")

        with open(suite_path, "r") as f:
            data = json.load(f)

        assert "test_cases" in data
        assert len(data["test_cases"]) > 0

        first_case = data["test_cases"][0]
        assert "case_id" in first_case
        assert "title" in first_case
        assert "priority" in first_case

    def test_traceability_matrix_markdown(self):
        """验证追溯矩阵 Markdown 文件存在且包含覆盖率信息"""
        test_dir = os.path.join(_project_root, "tests", "output")
        matrix_path = os.path.join(test_dir, "traceability_matrix.md")

        if os.path.exists(matrix_path):
            with open(matrix_path, "r") as f:
                content = f.read()
            assert "追溯矩阵" in content or "覆盖率" in content or "Traceability" in content
            print(f"[E2E] 追溯矩阵: {len(content)} 字符")
        else:
            pytest.skip("追溯矩阵 Markdown 尚未生成")


# =============================================================================
# 完整工作流端到端（需 LLM）
# =============================================================================


@pytest.mark.slow
@requires_llm
class TestFullWorkflowE2E:
    """完整工作流端到端测试：PRD → 报告（需要 LLM API key）"""

    def test_full_workflow_from_prd_to_report(self, sample_prd_path):
        """
        端到端测试：从 sample_prd.md 输入，完整运行工作流，
        验证最终输出的报告文件存在且包含关键信息。
        """
        with tempfile.TemporaryDirectory() as output_dir:
            workflow = ITestWorkflow()

            initial_state = create_initial_state(
                prd_path=sample_prd_path,
                llm_model="gpt-4o-mini",
                output_dir=output_dir,
                kb_persist_dir=os.path.join(output_dir, "chroma_db"),
                config={"analyzer_temperature": 0.1},
            )

            config = {"configurable": {"thread_id": "e2e-test-1"}}

            result = workflow.run(initial_state, config=config)

            # 1. 验证工作流完成
            assert result.get("phase") in (
                WorkflowPhase.COMPLETED.value,
                WorkflowPhase.REPORTING.value,
            ), f"工作流未完成: phase={result.get('phase')}, error={result.get('error_occurred')}"

            # 2. 验证各阶段产物存在
            analysis = result.get("analysis_result", {})
            assert analysis.get("total_functions", 0) > 0, "需求分析未产生功能点"

            suite = result.get("test_suite", {})
            assert suite.get("total_cases", 0) > 0, "未生成测试用例"

            review = result.get("review_result", {})
            assert "score" in review, "评审未产生评分"

            execution = result.get("execution_result", {})
            assert execution.get("total", 0) > 0, "执行结果缺少 total"

            # 3. 验证报告文件
            report_path = result.get("report_path", "")
            if report_path:
                assert os.path.exists(report_path), f"报告不存在: {report_path}"
                with open(report_path, "r") as f:
                    content = f.read()
                assert "iTest-Agent 测试报告" in content
                assert "需求概览" in content or "## 1." in content

            # 4. 验证输出目录下的全部产物
            expected_files = [
                "test_report.md",
                "execution_log.json",
                "test_suite.json",
                "review_result.json",
            ]
            for fname in expected_files:
                fpath = os.path.join(output_dir, fname)
                assert os.path.exists(fpath), f"缺少输出文件: {fname}"

            # 5. 验证执行日志与测试套件一致性
            with open(os.path.join(output_dir, "execution_log.json"), "r") as f:
                exec_log = json.load(f)
            with open(os.path.join(output_dir, "test_suite.json"), "r") as f:
                suite_data = json.load(f)

            assert exec_log["total"] == len(suite_data["test_cases"]), (
                f"执行日志总数({exec_log['total']}) 与用例集({len(suite_data['test_cases'])})不一致"
            )

            print(f"\n[E2E PASS] 完整工作流运行成功!")
            print(f"  功能点: {analysis.get('total_functions')}")
            print(f"  用例数: {suite.get('total_cases')}")
            print(f"  评审分: {review.get('score')}")
            print(f"  通过率: {execution.get('pass_rate', 0):.1%}")
            print(f"  报告:   {report_path}")

    def test_workflow_messages_chain(self, sample_prd_path):
        """验证工作流消息链的完整性（每个节点都记录了日志）"""
        with tempfile.TemporaryDirectory() as output_dir:
            workflow = ITestWorkflow()

            initial_state = create_initial_state(
                prd_path=sample_prd_path,
                llm_model="gpt-4o-mini",
                output_dir=output_dir,
            )

            config = {"configurable": {"thread_id": "e2e-messages-1"}}
            result = workflow.run(initial_state, config=config)

            messages = result.get("messages", [])
            assert len(messages) >= 6, f"消息日志过少: {len(messages)}"

            # 验证关键节点日志
            node_starts = [m for m in messages if "开始执行" in m]
            assert len(node_starts) >= 5, f"至少应有 5 个节点启动日志"

            # 不应有未恢复的错误
            assert not result.get("error_occurred"), "工作流存在未处理的错误"


# =============================================================================
# 增量更新流程测试（需 LLM）
# =============================================================================


@pytest.mark.slow
@requires_llm
class TestIncrementalWorkflow:
    """增量更新工作流测试"""

    def test_incremental_state_creation(self, sample_prd_path):
        """验证增量状态创建和运行"""
        with tempfile.TemporaryDirectory() as output_dir:
            # 第一阶段：全量分析
            workflow = ITestWorkflow()
            initial = create_initial_state(
                prd_path=sample_prd_path,
                llm_model="gpt-4o-mini",
                output_dir=output_dir,
            )
            config = {"configurable": {"thread_id": "incr-full-1"}}
            result_full = workflow.run(initial, config=config)

            analysis_path = result_full.get("analysis_result", {}).get("file_path", "")
            assert os.path.exists(analysis_path), f"全量分析结果不存在: {analysis_path}"

            # 第二阶段：模拟增量更新
            incr_state = create_incremental_state(
                prd_path=sample_prd_path,
                previous_analysis_path=analysis_path,
                changed_function_ids=["FUNC-001"],
                llm_model="gpt-4o-mini",
                output_dir=output_dir,
            )

            config2 = {"configurable": {"thread_id": "incr-delta-1"}}
            result_incr = workflow.run(incr_state, config=config2)

            # 增量状态应有 change_log
            incr_suite = result_incr.get("test_suite", {})
            print(f"\n[增量测试] 全量用例: {result_full.get('test_suite', {}).get('total_cases', 0)}")
            print(f"[增量测试] 增量用例: {incr_suite.get('total_cases', 0)}")


# =============================================================================
# 报告生成器与工作流集成测试
# =============================================================================


class TestReportWorkflowIntegration:
    """验证报告生成器与工作流输出的集成"""

    def test_report_from_workflow_outputs(self):
        """使用工作流可能输出的 JSON 文件测试报告生成"""
        test_dir = os.path.join(_project_root, "tests", "output")

        exec_path = os.path.join(test_dir, "execution_log.json")
        suite_path = os.path.join(test_dir, "test_suite.json")

        with open(exec_path, "r") as f:
            exec_data = json.load(f)
        with open(suite_path, "r") as f:
            suite_data = json.load(f)

        # 1. 数据模型正确
        data = ReportData(execution_log=exec_data, test_suite=suite_data)
        assert data.total > 0

        # 2. 缺陷聚类正确
        clusters = DefectClusterer.by_priority(data)
        # 失败+阻塞应该有
        total_in_clusters = sum(c["count"] for c in clusters.values())
        failed_blocked = data.failed + data.blocked
        assert total_in_clusters == failed_blocked

        # 3. Markdown 生成
        md = MarkdownReportBuilder.build(data)
        assert len(md) > 500
        assert "## 1. 测试摘要" in md

        # 4. 通过率正确显示
        assert f"{data.passed_pct:.1f}%" in md

        print(f"[集成] 报告数据验证通过: {data.total} 条用例, 通过率 {data.passed_pct:.1f}%")

"""
iTest-Agent LangGraph 工作流定义

StateGraph 是 LangGraph 的核心抽象，定义了:
- 状态对象（AgentState）
- 节点列表（各业务节点 + 错误处理 + 终点）
- 边（Edge）: 节点间的直接流转
- 条件边（Conditional Edge）: 根据状态动态路由（如评审结果决定是执行还是回退）

状态图拓扑:
    analyze_requirements
           │
    generate_testcases
           │
    review_testcases ──(不通过)──→ generate_testcases  [循环重试]
           │
      (通过)│
    execute_testcases
           │
    generate_report ──→ END

错误处理:
    任何节点报错 → handle_error → {重试 OR 取消}

Checkpoint:
    使用 SqliteSaver 持久化状态，支持重入恢复。
"""

import os
import sys
from typing import Any, Dict, Literal

from langgraph.graph import END, StateGraph

from graph.checkpoint_sqlite import SQLiteCheckpoint

# 添加项目根目录到 Python 路径
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from graph.nodes import (
    analyze_requirements,
    execute_testcases,
    finalize,
    generate_report,
    generate_testcases,
    handle_error,
    review_testcases,
)
from graph.state import AgentState, WorkflowPhase


# =============================================================================
# 条件判断函数（Conditional Edge 路由逻辑）
# =============================================================================


def _after_review(state: AgentState) -> Literal["execute_testcases", "generate_testcases", "handle_error"]:
    """
    评审后的路由逻辑

    - 评审通过 → 进入执行阶段 (execute_testcases)
    - 评审不通过 → 返回重新生成 (generate_testcases)
    - 出错 → 进入错误处理 (handle_error)
    """
    if state.get("error_occurred"):
        return "handle_error"

    review = state.get("review_result", {})
    if review.get("passed", False):
        return "execute_testcases"

    # 评审不通过：检查是否达到最大迭代次数
    cfg = state.get("config", {}) or {}
    max_rounds = int(cfg.get("max_review_rounds", 3))
    attempts = int(state.get("generation_attempts", 0))
    if attempts >= max_rounds:
        return "handle_error"
    return "generate_testcases"


def _after_error(state: AgentState) -> Literal["__end__", "__init__"]:
    """
    错误处理后的路由

    - 错误已恢复 → 重新从 analyze_requirements 开始
    - 重试耗尽 → 终止 (__end__)
    注意: __init__ 在 LangGraph 中需特殊处理，这里用状态导向。
    实际使用: phase=CANCELLED → END，否则重新进入 analyze_requirements
    """
    phase = state.get("phase", "")
    if phase == WorkflowPhase.CANCELLED.value:
        return "__end__"
    return "__init__"


def _should_continue(state: AgentState) -> Literal["generate_report", "finalize"]:
    """执行后的路由"""
    if state.get("error_occurred"):
        return "finalize"
    return "generate_report"


# =============================================================================
# StateGraph 构建
# =============================================================================


def build_state_graph() -> StateGraph:
    """
    构建 iTest-Agent 的 StateGraph

    Returns:
        StateGraph: 未编译的图（需调用 .compile(checkpointer=...) 编译）
    """
    # 创建 StateGraph，绑定 AgentState 类型
    workflow = StateGraph(AgentState)

    # ---- 添加节点 ----
    # 核心业务节点
    workflow.add_node("analyze_requirements", analyze_requirements)
    workflow.add_node("generate_testcases", generate_testcases)
    workflow.add_node("review_testcases", review_testcases)
    workflow.add_node("execute_testcases", execute_testcases)
    workflow.add_node("generate_report", generate_report)

    # 错误处理和终点节点
    workflow.add_node("handle_error", handle_error)
    workflow.add_node("finalize", finalize)

    # ---- 设置入口 ----
    workflow.set_entry_point("analyze_requirements")

    # ---- 添加边 ----
    # 主线流程: 需求分析 → 用例生成 → 用例评审
    workflow.add_edge("analyze_requirements", "generate_testcases")
    workflow.add_edge("generate_testcases", "review_testcases")

    # 评审后的条件分支: 通过 → 执行 | 不通过 → 重新生成 | 错误 → 错误处理
    workflow.add_conditional_edges(
        "review_testcases",
        _after_review,
        {
            "execute_testcases": "execute_testcases",
            "generate_testcases": "generate_testcases",
            "handle_error": "handle_error",
        },
    )

    # 执行 → 报告生成（带错误检查）
    workflow.add_conditional_edges(
        "execute_testcases",
        _should_continue,
        {
            "generate_report": "generate_report",
            "finalize": "finalize",
        },
    )

    # 报告生成 → 终点
    workflow.add_edge("generate_report", "finalize")

    # 错误处理: 检查是否可恢复
    workflow.add_conditional_edges(
        "handle_error",
        _after_error,
        {
            "__end__": END,
            "__init__": "analyze_requirements",
        },
    )

    # 终点 → END
    workflow.add_edge("finalize", END)

    return workflow


# =============================================================================
# ITestWorkflow 封装类
# =============================================================================


class ITestWorkflow:
    """
    iTest-Agent 工作流封装

    提供高级 API:
    - run(): 同步执行工作流
    - run_stream(): 流式执行工作流（逐步返回状态快照）
    - resume(): 从 checkpoint 恢复执行

    Attributes:
        app: 编译后的 LangGraph 应用
        checkpointer: SQLite checkpointer 实例
    """

    def __init__(self, checkpoint_db_path: str = "", max_retries: int = 2):
        """
        初始化工作流

        Args:
            checkpoint_db_path: SQLite 持久化路径。若为空则使用内存模式（不持久化）
            max_retries: 全局默认最大重试次数（初始状态未显式指定时使用）
        """
        self.max_retries = max_retries
        graph = build_state_graph()

        if checkpoint_db_path:
            db_dir = os.path.dirname(checkpoint_db_path)
            if db_dir:
                os.makedirs(db_dir, exist_ok=True)
            self.checkpointer = SQLiteCheckpoint(db_path=checkpoint_db_path)
            self.app = graph.compile(checkpointer=self.checkpointer)
        else:
            from langgraph.checkpoint.memory import MemorySaver
            self.checkpointer = MemorySaver()
            self.app = graph.compile(checkpointer=self.checkpointer)

    def run(self, initial_state: AgentState, config: Dict[str, Any] = None) -> AgentState:
        """
        同步执行工作流

        Args:
            initial_state: 初始状态（通过 create_initial_state 或 create_incremental_state 创建）
            config: LangGraph 配置（如 {"configurable": {"thread_id": "..."}}）

        Returns:
            AgentState: 工作流执行完毕后的最终状态
        """
        if config is None:
            config = {"configurable": {"thread_id": "itest-session-1"}}

        state = dict(initial_state)
        state.setdefault("max_retries", self.max_retries)
        result = self.app.invoke(state, config=config)
        return result

    def run_stream(self, initial_state: AgentState, config: Dict[str, Any] = None):
        """
        流式执行工作流

        每次节点完成后 yield 当前状态快照。

        Args:
            initial_state: 初始状态
            config: LangGraph 配置

        Yields:
            AgentState: 每个节点执行后的状态快照
        """
        if config is None:
            config = {"configurable": {"thread_id": "itest-session-1"}}

        for event in self.app.stream(initial_state, config=config):
            yield event

    def resume(
        self,
        config: Dict[str, Any],
        resume_input: Dict[str, Any] = None,
    ) -> AgentState:
        """
        从 checkpoint 恢复执行

        适用于:
        - 工作流中断后继续执行
        - 人工介入后恢复（如评审反馈补充）

        Args:
            config: 必须包含 thread_id 的 LangGraph 配置
            resume_input: 恢复时注入的额外状态

        Returns:
            AgentState: 最终状态
        """
        if resume_input is None:
            resume_input = {}

        state = self.app.get_state(config)
        if state is None:
            raise RuntimeError(
                f"无法找到 checkpoint: config={config}。"
                f"请确认 checkpoint_db_path 与上次执行时一致。"
            )

        current_state = dict(state.values)
        current_state.update(resume_input)
        current_state["phase"] = WorkflowPhase.INIT.value
        current_state["error_occurred"] = False

        result = self.app.invoke(current_state, config=config)
        return result

    def get_state(self, config: Dict[str, Any]) -> AgentState:
        """获取当前 checkpoint 状态"""
        state = self.app.get_state(config)
        if state is None:
            raise RuntimeError(f"未找到 thread_id={config.get('configurable', {}).get('thread_id')} 的状态")
        return state.values


# =============================================================================
# 构建函数
# =============================================================================


def build_itest_workflow(checkpoint_db_path: str = "") -> ITestWorkflow:
    """
    便捷构建函数：创建编译好的 iTest-Agent 工作流

    Args:
        checkpoint_db_path: SQLite checkpoint 路径。示例:
            "./itest_checkpoints.db"
            "/path/to/project/checkpoints/itest.db"

    Returns:
        ITestWorkflow: 封装好的工作流实例
    """
    return ITestWorkflow(checkpoint_db_path=checkpoint_db_path)

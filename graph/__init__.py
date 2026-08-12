"""
iTest-Agent LangGraph 工作流模块

基于 LangGraph 的多 Agent 协作状态图，实现：
- 需求分析 → 用例生成 → 用例评审 → 用例执行 → 报告生成 全流程编排
- 评审不通过自动回退重新生成（条件分支）
- 节点级错误处理和自动重试
- Checkpoint 持久化支持状态恢复
- 增量更新（需求变更仅重新生成受影响用例）

使用方式:
    from graph.workflow import build_itest_workflow
    app = build_itest_workflow(checkpoint_db_path="./itest_checkpoints.db")
    result = app.invoke(initial_state)
"""

from graph.state import AgentState, WorkflowPhase, ErrorInfo, ReviewResult
from graph.workflow import build_itest_workflow, ITestWorkflow

__all__ = [
    "AgentState",
    "WorkflowPhase",
    "ErrorInfo",
    "ReviewResult",
    "build_itest_workflow",
    "ITestWorkflow",
]

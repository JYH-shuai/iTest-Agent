"""
iTest-Agent LangGraph 状态定义

AgentState 是整个工作流图的共享状态对象，所有节点读写同一份状态。
设计原则：
- 所有字段均可为空/默认值，支持局部更新
- error_history 记录每次错误，支持追溯
- change_log 记录增量变更，支持需求变更时的局部重新生成
- 状态可序列化为 JSON，支持 checkpoint 持久化
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Annotated, Any, Dict, List, Optional, Sequence, TypedDict

from langgraph.graph.message import add_messages


# =============================================================================
# 枚举常量
# =============================================================================


class WorkflowPhase(str, Enum):
    """工作流阶段枚举"""
    INIT = "init"                          # 初始状态
    ANALYZING = "analyzing"                # 需求分析中
    GENERATING = "generating"              # 用例生成中
    REVIEWING = "reviewing"                # 用例评审中
    EXECUTING = "executing"                # 用例执行中
    REPORTING = "reporting"                # 报告生成中
    ERROR = "error"                        # 错误处理
    COMPLETED = "completed"                # 流程完成
    CANCELLED = "cancelled"                # 流程取消


class ChangeType(str, Enum):
    """需求变更类型"""
    NONE = "none"                          # 无变更（首次运行）
    FULL_UPDATE = "full_update"            # 全量更新（需求整体变更）
    INCREMENTAL = "incremental"            # 增量更新（部分功能变更）
    ADD_FUNCTION = "add_function"          # 新增功能
    REMOVE_FUNCTION = "remove_function"    # 删除功能


# =============================================================================
# 数据结构
# =============================================================================


class ErrorInfo(TypedDict, total=False):
    """单次错误信息"""
    node: str                              # 错误发生的节点名称
    phase: str                             # 错误发生阶段
    error_type: str                        # 错误类型（Exception 类名）
    error_message: str                     # 错误消息
    timestamp: str                         # 错误时间（ISO 8601）
    attempt: int                           # 当前节点已尝试次数
    max_retries: int                       # 最大重试次数
    recovered: bool                        # 是否已恢复


class ReviewResult(TypedDict, total=False):
    """用例评审结果"""
    passed: bool                           # 是否通过
    score: float                           # 评审分数（0.0 ~ 100.0）
    total_cases: int                       # 评审用例总数
    passed_cases: int                      # 通过用例数
    failed_cases: int                      # 未通过用例数
    feedback: str                          # 评审反馈（改进建议）
    failed_case_ids: List[str]             # 未通过用例 ID 列表
    coverage_gaps: List[str]               # 覆盖度缺口


class AnalysisResult(TypedDict, total=False):
    """需求分析结果摘要（存入状态供后续节点使用）"""
    file_path: str                         # 分析结果 JSON 路径
    product_name: str                      # 产品名称
    module_name: str                       # 模块名称
    total_functions: int                   # 功能总数
    p0_count: int                          # P0 功能数
    p1_count: int                          # P1 功能数
    p2_count: int                          # P2 功能数


class TestSuiteInfo(TypedDict, total=False):
    """测试用例集信息摘要"""
    file_path: str                         # 用例集 JSON 路径
    suite_name: str                        # 套件名称
    total_cases: int                       # 用例总数
    p0_count: int                          # P0 用例数
    p1_count: int                          # P1 用例数
    p2_count: int                          # P2 用例数
    traceability_matrix_path: str          # 追溯矩阵 JSON 路径


class ExecutionResult(TypedDict, total=False):
    """执行结果摘要"""
    total: int                             # 总用例数
    passed: int                            # 通过数
    failed: int                            # 失败数
    blocked: int                           # 阻塞数
    skipped: int                           # 跳过数
    pass_rate: float                       # 通过率
    duration_seconds: float                # 总耗时
    log_path: str                          # 执行日志路径


class IncrementalChange(TypedDict, total=False):
    """增量变更记录"""
    change_type: str                       # ChangeType 值
    changed_functions: List[str]           # 变更的功能 ID 列表
    previous_analysis_path: str            # 变更前分析结果路径
    timestamp: str                         # 变更时间


# =============================================================================
# AgentState — LangGraph 核心状态
# =============================================================================


class AgentState(TypedDict, total=False):
    """
    iTest-Agent 共享状态

    所有节点共享同一份状态字典，节点通过返回部分字段来实现增量更新。
    LangGraph 的 `TypedDict` + `Annotated` 支持列表字段的追加语义。

    字段分组说明:
    - 输入配置: prd_path, config
    - 工作流控制: phase, change_type, error_occurred, max_retries
    - 各阶段产出: analysis_result, test_suite, review_result, execution_result, report_path
    - 错误与追踪: error_history, messages, change_log
    - 外部依赖: llm_model, kb_persist_dir
    """

    # ---- 输入与配置 ----
    prd_path: str                          # PRD 需求文档路径
    config: Dict[str, Any]                 # 工作流配置（model、temperature 等）

    # ---- 工作流控制 ----
    phase: str                             # 当前阶段 (WorkflowPhase)
    change_type: str                       # 变更类型 (ChangeType)
    error_occurred: bool                   # 当前节点是否发生错误
    max_retries: int                       # 全局最大重试次数（默认 2）
    generation_attempts: int               # 用例生成尝试次数（评审回退计数，默认 0）

    # ---- 各阶段产出 ----
    analysis_result: AnalysisResult        # 需求分析结果摘要
    test_suite: TestSuiteInfo              # 用例集信息
    review_result: ReviewResult            # 评审结果
    execution_result: ExecutionResult      # 执行结果
    report_path: str                       # 测试报告路径

    # ---- 增量变更 ----
    change_log: List[IncrementalChange]    # 变更日志

    # ---- 错误与追踪 ----
    error_history: List[ErrorInfo]         # 错误历史记录
    messages: Annotated[Sequence[str], add_messages]  # 工作流消息日志

    # ---- 外部依赖 ----
    llm_model: str                         # 默认 LLM 模型名称
    kb_persist_dir: str                    # Chroma 知识库持久化目录
    output_dir: str                        # 输出目录
    checkpoint_db_path: str                # Checkpoint SQLite 路径


# =============================================================================
# 状态工厂函数
# =============================================================================


def create_initial_state(
    prd_path: str,
    llm_model: str = "gpt-4o-mini",
    output_dir: str = "",
    kb_persist_dir: str = "",
    checkpoint_db_path: str = "",
    config: Optional[Dict[str, Any]] = None,
    change_type: str = "none",
    previous_analysis_path: str = "",
) -> AgentState:
    """
    创建初始工作流状态

    Args:
        prd_path: PRD 需求文档路径（必填）
        llm_model: LLM 模型名称
        output_dir: 产物输出目录
        kb_persist_dir: 知识库目录
        checkpoint_db_path: Checkpoint SQLite 路径
        config: 额外配置字典
        change_type: 变更类型（用于增量更新场景）
        previous_analysis_path: 前次分析结果路径（增量更新时使用）

    Returns:
        AgentState: 初始状态字典
    """
    state: AgentState = {
        "prd_path": prd_path,
        "phase": WorkflowPhase.INIT.value,
        "change_type": change_type,
        "error_occurred": False,
        "max_retries": 2,
        "generation_attempts": 0,
        "analysis_result": {},
        "test_suite": {},
        "review_result": {},
        "execution_result": {},
        "report_path": "",
        "change_log": [],
        "error_history": [],
        "messages": [],
        "llm_model": llm_model,
        "kb_persist_dir": kb_persist_dir,
        "output_dir": output_dir,
        "checkpoint_db_path": checkpoint_db_path,
        "config": config or {},
    }

    # 如果是增量更新，记录变更日志
    if change_type != ChangeType.NONE.value and previous_analysis_path:
        state["change_log"] = [
            {
                "change_type": change_type,
                "changed_functions": [],
                "previous_analysis_path": previous_analysis_path,
                "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
        ]

    return state


def create_incremental_state(
    prd_path: str,
    previous_analysis_path: str,
    changed_function_ids: List[str],
    change_type: str = "incremental",
    **kwargs,
) -> AgentState:
    """
    创建增量更新的初始状态

    Args:
        prd_path: 更新后的 PRD 路径
        previous_analysis_path: 前次分析结果 JSON 路径
        changed_function_ids: 变更的功能 ID 列表
        change_type: 变更类型
        **kwargs: 传递给 create_initial_state 的其他参数

    Returns:
        AgentState: 增量更新状态
    """
    state = create_initial_state(
        prd_path=prd_path,
        change_type=change_type,
        previous_analysis_path=previous_analysis_path,
        **kwargs,
    )
    # 记录具体变更的功能 ID
    if state.get("change_log"):
        state["change_log"][0]["changed_functions"] = changed_function_ids

    return state

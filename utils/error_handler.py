"""
错误分类与处理模块

提供统一的错误分类、上下文封装和恢复策略，供工作流节点和
``handle_error`` 路由使用。

核心组件:
    - ``ErrorCategory``: 错误三分类（可重试 / 致命 / 降级）
    - ``ErrorContext``: 错误上下文数据类
    - ``ErrorRecoveryStrategy``: 恢复策略枚举（RETRY / ABORT / SKIP_NODE / FALLBACK）
    - ``classify_error``: 错误分类函数

典型用法::

    from utils.error_handler import classify_error, ErrorCategory, ErrorContext

    try:
        ...
    except Exception as e:
        ctx = ErrorContext(node_name="analyze_requirements", exception=e)
        category = classify_error(ctx)
        if category == ErrorCategory.RETRYABLE:
            raise  # 交由装饰器重试
        elif category == ErrorCategory.DEGRADED:
            ...  # 降级继续
"""

from __future__ import annotations

import traceback
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional

from utils.retry import is_retryable


# =============================================================================
# ErrorCategory — 错误分类
# =============================================================================


class ErrorCategory(str, Enum):
    """错误分类枚举。

    - ``RETRYABLE``: 可重试 — 网络超时、连接重置等临时错误。
    - ``FATAL``: 致命 — 文件不存在、权限不足、参数错误等不可恢复错误。
    - ``DEGRADED``: 降级 — 非关键错误，跳过当前步骤继续执行。
    """

    RETRYABLE = "retryable"
    FATAL = "fatal"
    DEGRADED = "degraded"


# =============================================================================
# ErrorRecoveryStrategy — 恢复动作
# =============================================================================


class ErrorRecoveryStrategy(str, Enum):
    """错误恢复策略枚举。

    决定 ``handle_error`` 节点如何路由：

    - ``RETRY``: 返回原节点重试。
    - ``ABORT``: 终止整个工作流（→ CANCELLED）。
    - ``SKIP_NODE``: 跳过当前节点，尝试继续下一节点。
    - ``FALLBACK``: 走降级路径（如用缓存数据代替新鲜结果）。
    """

    RETRY = "retry"
    ABORT = "abort"
    SKIP_NODE = "skip_node"
    FALLBACK = "fallback"


# =============================================================================
# ErrorContext — 错误上下文
# =============================================================================


@dataclass
class ErrorContext:
    """错误上下文，用于分类判定。

    Attributes:
        node_name: 发生错误的节点名称（如 ``"analyze_requirements"``）。
        phase: 工作流阶段（如 ``"analyzing"``）。
        attempt: 当前已尝试次数（从 1 开始）。
        max_retries: 全局最大重试次数。
        exception: 捕获到的异常实例。
    """

    node_name: str = ""
    phase: str = ""
    attempt: int = 1
    max_retries: int = 2
    exception: Optional[BaseException] = None


# =============================================================================
# classify_error — 分类函数
# =============================================================================


def classify_error(ctx: ErrorContext) -> ErrorCategory:
    """根据异常类型和上下文分类错误。

    分类规则（按优先级）：
    1. 无异常 → ``DEGRADED``（非错误场景，降级继续）
    2. 不可重试异常（FileNotFound / PermissionError / ValueError 等）→ ``FATAL``
    3. 重试次数已耗尽 → ``FATAL``（即使异常本身可重试）
    4. 可重试异常（网络/超时/IO）→ ``RETRYABLE``
    5. 其他未知异常 → ``FATAL``（保守策略）

    Args:
        ctx: 错误上下文。

    Returns:
        错误分类。
    """
    if ctx.exception is None:
        return ErrorCategory.DEGRADED

    exc = ctx.exception

    # 1. 先判定是否为不可重试的致命错误
    if not is_retryable(exc):
        return ErrorCategory.FATAL

    # 2. 检查重试次数是否已耗尽
    if ctx.attempt > ctx.max_retries:
        return ErrorCategory.FATAL

    # 3. 可重试
    return ErrorCategory.RETRYABLE


# =============================================================================
# 策略映射 — 将 ErrorCategory 映射为 ErrorRecoveryStrategy
# =============================================================================


def category_to_strategy(category: ErrorCategory) -> ErrorRecoveryStrategy:
    """将错误分类转换为恢复策略。

    Args:
        category: 错误分类。

    Returns:
        对应的恢复策略。
    """
    _map: Dict[ErrorCategory, ErrorRecoveryStrategy] = {
        ErrorCategory.RETRYABLE: ErrorRecoveryStrategy.RETRY,
        ErrorCategory.FATAL: ErrorRecoveryStrategy.ABORT,
        ErrorCategory.DEGRADED: ErrorRecoveryStrategy.SKIP_NODE,
    }
    return _map.get(category, ErrorRecoveryStrategy.ABORT)


def determine_recovery_strategy(
    category: ErrorCategory,
    ctx: Optional[ErrorContext] = None,
) -> ErrorRecoveryStrategy:
    """根据错误分类和上下文确定下一步恢复动作。

    特殊场景覆盖：
    - ``DEGRADED`` + 节点是 ``execute_testcases`` → ``SKIP_NODE``（单条用例失败不中断整体）
    - ``DEGRADED`` + 其他节点 → ``FALLBACK``（尝试降级路径）

    Args:
        category: 错误分类。
        ctx: 错误上下文（可选，用于节点感知）。

    Returns:
        恢复策略。
    """
    if category == ErrorCategory.DEGRADED:
        if ctx and ctx.node_name == "execute_testcases":
            return ErrorRecoveryStrategy.SKIP_NODE
        return ErrorRecoveryStrategy.FALLBACK

    return category_to_strategy(category)

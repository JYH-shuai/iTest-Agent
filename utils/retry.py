"""
重试机制核心模块

提供同步/异步重试装饰器，支持指数退避（exponential backoff）、
jitter 抖动以及可重试异常白名单。

典型用法::

    from utils.retry import retry_on_failure, RetryConfig

    config = RetryConfig(max_retries=3, base_delay=1.0, max_delay=60.0)

    @retry_on_failure(config=config)
    def flaky_network_call():
        ...

异常分类:
    - **可重试**: 网络错误（ConnectionError, TimeoutError）、临时性错误
      （HTTP 5xx）、资源暂时不可用
    - **不可重试**: 文件不存在（FileNotFoundError）、权限不足（PermissionError）、
      参数错误（ValueError, TypeError）、断言失败（AssertionError）
"""

from __future__ import annotations

import asyncio
import functools
import random
import time
import traceback
from dataclasses import dataclass, field
from typing import (
    Any,
    Callable,
    Dict,
    List,
    Optional,
    Set,
    Tuple,
    Type,
    TypeVar,
    Union,
)

# ---- 类型变量 ----------------------------------------------------------------
F = TypeVar("F", bound=Callable[..., Any])
AF = TypeVar("AF", bound=Callable[..., Any])  # Async callable


# =============================================================================
# RetryConfig — 重试配置
# =============================================================================


@dataclass
class RetryConfig:
    """重试策略配置

    Attributes:
        max_retries: 最大重试次数（不含首次调用）。0 表示不重试。
        base_delay: 初始退避延迟（秒）。
        max_delay: 最大退避延迟上限（秒）。
        jitter: 是否在退避延迟上叠加随机抖动以分散重试风暴。
        retryable_exceptions: 可重试异常类型的白名单。为空则仅对
            ``is_retryable()`` 返回 True 的异常重试。
    """

    max_retries: int = 3
    base_delay: float = 1.0
    max_delay: float = 60.0
    jitter: bool = True
    retryable_exceptions: Tuple[Type[BaseException], ...] = field(default_factory=tuple)


# ---- 默认配置 ----------------------------------------------------------------

DEFAULT_RETRY_CONFIG = RetryConfig()


# =============================================================================
# is_retryable — 异常可重试判定
# =============================================================================


# 可重试异常白名单：网络/IO 层临时性错误
_RETRYABLE_EXCEPTIONS: Set[Type[BaseException]] = {
    ConnectionError,
    ConnectionRefusedError,
    ConnectionResetError,
    ConnectionAbortedError,
    TimeoutError,
    OSError,  # 包含 BrokenPipeError 等临时 IO 错误
    BlockingIOError,
    InterruptedError,
}

# 不可重试异常：逻辑/输入/权限错误
_NON_RETRYABLE_EXCEPTIONS: Set[Type[BaseException]] = {
    FileNotFoundError,
    FileExistsError,
    NotADirectoryError,
    IsADirectoryError,
    PermissionError,
    ValueError,
    TypeError,
    KeyError,
    IndexError,
    AttributeError,
    AssertionError,
    NotImplementedError,
    SyntaxError,
    ImportError,
    ModuleNotFoundError,
    NameError,
    UnboundLocalError,
    RecursionError,
    MemoryError,
    SystemExit,
    KeyboardInterrupt,
}


def is_retryable(exc: BaseException) -> bool:
    """判断异常是否属于可重试类型。

    判定规则（按优先级）：
    1. 若异常类型或其任意父类属于不可重试白名单 → ``False``
    2. 若异常类型或其任意父类属于可重试白名单 → ``True``
    3. 默认 → ``False``（保守策略：未知异常不重试）

    Args:
        exc: 待判定的异常实例。

    Returns:
        ``True`` 表示可以安全重试。
    """
    exc_type = type(exc)

    # 先检查不可重试（优先级更高，避免误判安全重试）
    for non_retryable in _NON_RETRYABLE_EXCEPTIONS:
        if issubclass(exc_type, non_retryable):
            return False

    # 再检查可重试
    for retryable in _RETRYABLE_EXCEPTIONS:
        if issubclass(exc_type, retryable):
            return True

    # 默认不可重试
    return False


# =============================================================================
# 退避计算
# =============================================================================


def _compute_delay(attempt: int, config: RetryConfig) -> float:
    """计算指数退避延迟 + 可选 jitter。

    公式: ``min(base_delay * 2^(attempt-1), max_delay) [+ jitter]``

    Jitter 为 ±25% 均匀随机。

    Args:
        attempt: 当前重试次数（从 1 开始）。
        config: 重试配置。

    Returns:
        本次重试等待的秒数。
    """
    delay = min(config.base_delay * (2 ** (attempt - 1)), config.max_delay)
    if config.jitter:
        jitter_range = delay * 0.25
        delay += random.uniform(-jitter_range, jitter_range)
    return max(0.0, delay)


# =============================================================================
# _should_retry — 内部重试判定
# =============================================================================


def _should_retry(
    exc: BaseException,
    config: RetryConfig,
) -> bool:
    """综合判定是否应当重试。

    优先使用 config.retryable_exceptions，未配置时回退到全局 is_retryable()。

    Args:
        exc: 捕获到的异常。
        config: 重试配置。

    Returns:
        是否应重试。
    """
    if config.retryable_exceptions:
        return isinstance(exc, config.retryable_exceptions)
    return is_retryable(exc)


# =============================================================================
# retry_on_failure — 同步重试装饰器
# =============================================================================


def retry_on_failure(
    max_retries: Optional[int] = None,
    base_delay: Optional[float] = None,
    max_delay: Optional[float] = None,
    jitter: Optional[bool] = None,
    retryable_exceptions: Optional[Tuple[Type[BaseException], ...]] = None,
    config: Optional[RetryConfig] = None,
) -> Callable[[F], F]:
    """同步重试装饰器。

    支持两种传参方式：
    1. 通过 ``RetryConfig`` 对象：``@retry_on_failure(config=RetryConfig(...))``
    2. 通过关键字参数：``@retry_on_failure(max_retries=3, base_delay=1.0)``
    两者同时提供时以 ``config`` 为准。

    重试行为：
    - 捕获到可重试异常且重试次数未耗尽 → 退避等待后重新执行
    - 捕获到不可重试异常 → 立即抛出，不重试
    - 重试耗尽 → 抛出最后一次异常

    Args:
        max_retries: 最大重试次数。
        base_delay: 初始退避延迟（秒）。
        max_delay: 退避延迟上限（秒）。
        jitter: 是否启用抖动。
        retryable_exceptions: 可重试异常白名单。
        config: 完整的重试配置对象。

    Returns:
        装饰后的同步函数。

    Raises:
        RuntimeError: 重试耗尽时抛出，包含最后一次异常信息。
    """

    if config is None:
        resolved_config = RetryConfig(
            max_retries=max_retries if max_retries is not None else 3,
            base_delay=base_delay if base_delay is not None else 1.0,
            max_delay=max_delay if max_delay is not None else 60.0,
            jitter=jitter if jitter is not None else True,
            retryable_exceptions=retryable_exceptions or (),
        )
    else:
        resolved_config = config

    def decorator(func: F) -> F:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            last_exception: Optional[BaseException] = None

            for attempt in range(resolved_config.max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except Exception as exc:
                    last_exception = exc

                    # 不可重试 → 立即抛出
                    if not _should_retry(exc, resolved_config):
                        raise

                    # 已用完重试次数
                    if attempt >= resolved_config.max_retries:
                        raise RuntimeError(
                            f"重试耗尽（max_retries={resolved_config.max_retries}），"
                            f"最终错误: [{type(exc).__name__}] {exc}"
                        ) from exc

                    # 退避等待
                    delay = _compute_delay(attempt + 1, resolved_config)
                    time.sleep(delay)

            # 理论上不会走到这里，安全兜底
            if last_exception is not None:
                raise RuntimeError(
                    f"重试耗尽（max_retries={resolved_config.max_retries}），"
                    f"最终错误: [{type(last_exception).__name__}] {last_exception}"
                ) from last_exception

        return wrapper  # type: ignore[return-value]

    return decorator


# =============================================================================
# async_retry_on_failure — 异步重试装饰器
# =============================================================================


def async_retry_on_failure(
    max_retries: Optional[int] = None,
    base_delay: Optional[float] = None,
    max_delay: Optional[float] = None,
    jitter: Optional[bool] = None,
    retryable_exceptions: Optional[Tuple[Type[BaseException], ...]] = None,
    config: Optional[RetryConfig] = None,
) -> Callable[[AF], AF]:
    """异步重试装饰器。

    行为与 ``retry_on_failure`` 完全一致，但适用于 ``async def`` 函数。

    退避等待使用 ``asyncio.sleep`` 而非 ``time.sleep``，不阻塞事件循环。

    Args:
        max_retries: 最大重试次数。
        base_delay: 初始退避延迟（秒）。
        max_delay: 退避延迟上限（秒）。
        jitter: 是否启用抖动。
        retryable_exceptions: 可重试异常白名单。
        config: 完整的重试配置对象。

    Returns:
        装饰后的异步函数。
    """

    if config is None:
        resolved_config = RetryConfig(
            max_retries=max_retries if max_retries is not None else 3,
            base_delay=base_delay if base_delay is not None else 1.0,
            max_delay=max_delay if max_delay is not None else 60.0,
            jitter=jitter if jitter is not None else True,
            retryable_exceptions=retryable_exceptions or (),
        )
    else:
        resolved_config = config

    def decorator(func: AF) -> AF:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            last_exception: Optional[BaseException] = None

            for attempt in range(resolved_config.max_retries + 1):
                try:
                    return await func(*args, **kwargs)
                except Exception as exc:
                    last_exception = exc

                    if not _should_retry(exc, resolved_config):
                        raise

                    if attempt >= resolved_config.max_retries:
                        raise RuntimeError(
                            f"重试耗尽（max_retries={resolved_config.max_retries}），"
                            f"最终错误: [{type(exc).__name__}] {exc}"
                        ) from exc

                    delay = _compute_delay(attempt + 1, resolved_config)
                    await asyncio.sleep(delay)

            if last_exception is not None:
                raise RuntimeError(
                    f"重试耗尽（max_retries={resolved_config.max_retries}），"
                    f"最终错误: [{type(last_exception).__name__}] {last_exception}"
                ) from last_exception

        return wrapper  # type: ignore[return-value]

    return decorator

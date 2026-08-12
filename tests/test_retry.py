"""
utils.retry 与 utils.error_handler 模块测试

测试范围：
- retry_on_failure 装饰器的重试次数和退避逻辑
- async_retry_on_failure 异步版本
- 错误分类逻辑（is_retryable / classify_error）
- 不可重试异常不会触发重试
- RetryConfig 配置验证
"""

from __future__ import annotations

import asyncio
import time

import pytest

from utils.error_handler import (
    ErrorCategory,
    ErrorContext,
    ErrorRecoveryStrategy,
    category_to_strategy,
    classify_error,
    determine_recovery_strategy,
)
from utils.retry import (
    RetryConfig,
    async_retry_on_failure,
    is_retryable,
    retry_on_failure,
)


# =============================================================================
# is_retryable 测试
# =============================================================================


class TestIsRetryable:
    """测试 is_retryable 函数的异常分类"""

    @pytest.mark.parametrize(
        "exc",
        [
            ConnectionError("connection refused"),
            ConnectionRefusedError(),
            ConnectionResetError(),
            ConnectionAbortedError(),
            TimeoutError("timed out"),
            OSError("broken pipe"),
            BlockingIOError(),
            InterruptedError(),
        ],
    )
    def test_retryable_exceptions(self, exc: BaseException) -> None:
        """可重试异常应返回 True"""
        assert is_retryable(exc) is True, f"{type(exc).__name__} 应可重试"

    @pytest.mark.parametrize(
        "exc",
        [
            FileNotFoundError("file not found"),
            FileExistsError(),
            PermissionError("permission denied"),
            ValueError("invalid value"),
            TypeError("type mismatch"),
            KeyError("missing key"),
            IndexError("index out of range"),
            AttributeError("no attribute"),
            AssertionError("assertion failed"),
            NotImplementedError(),
            SyntaxError(),
            ImportError(),
            ModuleNotFoundError(),
            NameError(),
            MemoryError(),
            RecursionError(),
            SystemExit(),
            KeyboardInterrupt(),
        ],
    )
    def test_non_retryable_exceptions(self, exc: BaseException) -> None:
        """不可重试异常应返回 False"""
        assert is_retryable(exc) is False, (
            f"{type(exc).__name__} 不应可重试"
        )

    def test_custom_exception_default_false(self) -> None:
        """自定义异常默认不可重试"""

        class CustomBizError(Exception):
            pass

        assert is_retryable(CustomBizError("biz error")) is False


# =============================================================================
# RetryConfig 配置测试
# =============================================================================


class TestRetryConfig:
    """测试 RetryConfig 的默认值与自定义"""

    def test_default_values(self) -> None:
        cfg = RetryConfig()
        assert cfg.max_retries == 3
        assert cfg.base_delay == 1.0
        assert cfg.max_delay == 60.0
        assert cfg.jitter is True
        assert cfg.retryable_exceptions == ()

    def test_custom_values(self) -> None:
        cfg = RetryConfig(
            max_retries=5,
            base_delay=2.0,
            max_delay=30.0,
            jitter=False,
            retryable_exceptions=(ValueError,),
        )
        assert cfg.max_retries == 5
        assert cfg.base_delay == 2.0
        assert cfg.max_delay == 30.0
        assert cfg.jitter is False
        assert cfg.retryable_exceptions == (ValueError,)


# =============================================================================
# retry_on_failure 同步装饰器测试
# =============================================================================


class TestRetryOnFailure:
    """测试 retry_on_failure 同步装饰器"""

    def test_retries_and_succeeds(self) -> None:
        """前 2 次失败、第 3 次成功 → 应重试 2 次后返回"""
        call_count = [0]

        @retry_on_failure(max_retries=3, base_delay=0.01, jitter=False)
        def flaky_func() -> str:
            call_count[0] += 1
            if call_count[0] < 3:
                raise ConnectionError("transient error")
            return "success"

        result = flaky_func()
        assert result == "success"
        assert call_count[0] == 3

    def test_retry_exhausted_raises(self) -> None:
        """始终失败 → 重试耗尽抛 RuntimeError"""
        call_count = [0]

        @retry_on_failure(max_retries=2, base_delay=0.01, jitter=False)
        def always_fail() -> None:
            call_count[0] += 1
            raise ConnectionError("always down")

        with pytest.raises(RuntimeError, match="重试耗尽"):
            always_fail()
        # 首次调用 + 2 次重试 = 3
        assert call_count[0] == 3

    def test_non_retryable_no_retry(self) -> None:
        """不可重试异常（ValueError）→ 立即抛出，不重试"""
        call_count = [0]

        @retry_on_failure(max_retries=3, base_delay=0.01, jitter=False)
        def bad_input() -> None:
            call_count[0] += 1
            raise ValueError("invalid input")

        with pytest.raises(ValueError, match="invalid input"):
            bad_input()
        assert call_count[0] == 1  # 仅首次调用

    def test_custom_retryable_whitelist(self) -> None:
        """自定义白名单覆盖全局默认"""
        call_count = [0]

        @retry_on_failure(
            max_retries=2,
            base_delay=0.01,
            jitter=False,
            retryable_exceptions=(ValueError, KeyError),
        )
        def custom_flaky() -> str:
            call_count[0] += 1
            if call_count[0] < 3:
                raise ValueError("custom retryable")
            return "ok"

        result = custom_flaky()
        assert result == "ok"
        assert call_count[0] == 3

    def test_max_retries_zero_no_retry(self) -> None:
        """max_retries=0 → 不重试"""
        call_count = [0]

        @retry_on_failure(max_retries=0, base_delay=0.01)
        def no_retry_func() -> None:
            call_count[0] += 1
            raise ConnectionError("fail immediately")

        with pytest.raises(RuntimeError, match="重试耗尽"):
            no_retry_func()
        assert call_count[0] == 1

    def test_retry_config_object(self) -> None:
        """使用 RetryConfig 对象传参"""
        call_count = [0]
        cfg = RetryConfig(max_retries=2, base_delay=0.01, jitter=False)

        @retry_on_failure(config=cfg)
        def with_config() -> str:
            call_count[0] += 1
            if call_count[0] < 3:
                raise ConnectionError("temp")
            return "done"

        result = with_config()
        assert result == "done"
        assert call_count[0] == 3


# =============================================================================
# async_retry_on_failure 异步装饰器测试
# =============================================================================


class TestAsyncRetryOnFailure:
    """测试 async_retry_on_failure 异步装饰器"""

    @pytest.mark.asyncio
    async def test_async_retries_and_succeeds(self) -> None:
        call_count = [0]

        @async_retry_on_failure(max_retries=2, base_delay=0.01, jitter=False)
        async def flaky_async() -> str:
            call_count[0] += 1
            if call_count[0] < 3:
                raise TimeoutError("async timeout")
            return "async success"

        result = await flaky_async()
        assert result == "async success"
        assert call_count[0] == 3

    @pytest.mark.asyncio
    async def test_async_retry_exhausted(self) -> None:
        call_count = [0]

        @async_retry_on_failure(max_retries=1, base_delay=0.01, jitter=False)
        async def async_fail() -> None:
            call_count[0] += 1
            raise ConnectionError("async always down")

        with pytest.raises(RuntimeError, match="重试耗尽"):
            await async_fail()
        assert call_count[0] == 2

    @pytest.mark.asyncio
    async def test_async_non_retryable(self) -> None:
        call_count = [0]

        @async_retry_on_failure(max_retries=3, base_delay=0.01)
        async def async_bad() -> None:
            call_count[0] += 1
            raise ValueError("async bad input")

        with pytest.raises(ValueError):
            await async_bad()
        assert call_count[0] == 1


# =============================================================================
# classify_error 错误分类测试
# =============================================================================


class TestClassifyError:
    """测试 classify_error 和策略映射"""

    def test_none_exception_degraded(self) -> None:
        ctx = ErrorContext(node_name="test", exception=None)
        assert classify_error(ctx) == ErrorCategory.DEGRADED

    def test_retryable_network_error(self) -> None:
        ctx = ErrorContext(
            node_name="analyze_requirements",
            exception=ConnectionError("timeout"),
            attempt=1,
            max_retries=3,
        )
        assert classify_error(ctx) == ErrorCategory.RETRYABLE

    def test_fatal_permission_error(self) -> None:
        ctx = ErrorContext(
            node_name="generate_report",
            exception=PermissionError("access denied"),
        )
        assert classify_error(ctx) == ErrorCategory.FATAL

    def test_fatal_value_error(self) -> None:
        ctx = ErrorContext(
            node_name="execute_testcases",
            exception=ValueError("invalid param"),
        )
        assert classify_error(ctx) == ErrorCategory.FATAL

    def test_retry_exhausted_fatal(self) -> None:
        ctx = ErrorContext(
            node_name="generate_testcases",
            exception=TimeoutError("timeout"),
            attempt=4,
            max_retries=3,
        )
        assert classify_error(ctx) == ErrorCategory.FATAL

    def test_category_to_strategy_mapping(self) -> None:
        assert category_to_strategy(ErrorCategory.RETRYABLE) == ErrorRecoveryStrategy.RETRY
        assert category_to_strategy(ErrorCategory.FATAL) == ErrorRecoveryStrategy.ABORT
        assert category_to_strategy(ErrorCategory.DEGRADED) == ErrorRecoveryStrategy.SKIP_NODE

    def test_determine_recovery_execute_testcases_degraded(self) -> None:
        """execute_testcases 的 DEGRADED → SKIP_NODE"""
        ctx = ErrorContext(node_name="execute_testcases")
        strategy = determine_recovery_strategy(ErrorCategory.DEGRADED, ctx)
        assert strategy == ErrorRecoveryStrategy.SKIP_NODE

    def test_determine_recovery_other_degraded(self) -> None:
        """非 execute_testcases 的 DEGRADED → FALLBACK"""
        ctx = ErrorContext(node_name="analyze_requirements")
        strategy = determine_recovery_strategy(ErrorCategory.DEGRADED, ctx)
        assert strategy == ErrorRecoveryStrategy.FALLBACK

    def test_determine_recovery_retryable(self) -> None:
        strategy = determine_recovery_strategy(ErrorCategory.RETRYABLE)
        assert strategy == ErrorRecoveryStrategy.RETRY

    def test_determine_recovery_fatal(self) -> None:
        strategy = determine_recovery_strategy(ErrorCategory.FATAL)
        assert strategy == ErrorRecoveryStrategy.ABORT

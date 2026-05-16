from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict

from browser_use_bridge.browser.events import BrowserSecurityError


class ErrorCategory(Enum):
    RECOVERABLE = "recoverable"
    NON_RECOVERABLE = "non_recoverable"


class RecoveryStrategy(Enum):
    RETRY = "retry"
    SKIP = "skip"
    FALLBACK = "fallback"
    ABORT = "abort"


@dataclass(frozen=True)
class ErrorClassification:
    category: ErrorCategory
    reason: str


@dataclass(frozen=True)
class RetryAttempt:
    attempt: int
    operation: str
    success: bool
    category: ErrorCategory | None = None
    error_type: str | None = None
    error: str | None = None
    delay: float | None = None


class RetryConfig(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    base_delay: float = 0.25
    max_retries: int = 2
    backoff_factor: float = 2.0
    jitter: float = 0.1
    retryable_exceptions: tuple[type[BaseException], ...] = (
        TimeoutError,
        ConnectionError,
        OSError,
    )
    exhausted_strategy: RecoveryStrategy = RecoveryStrategy.ABORT


class RetryExhaustedError(RuntimeError):
    def __init__(self, summary: dict[str, Any]) -> None:
        self.summary = summary
        super().__init__(summary["final_error"])


def classify_error(error: BaseException) -> ErrorClassification:
    if isinstance(error, BrowserSecurityError):
        return ErrorClassification(
            category=ErrorCategory.NON_RECOVERABLE,
            reason="browser security policy blocked the operation",
        )
    if isinstance(error, (PermissionError, ValueError, TypeError, AssertionError)):
        return ErrorClassification(
            category=ErrorCategory.NON_RECOVERABLE,
            reason=f"{type(error).__name__} is not considered transient",
        )
    return ErrorClassification(
        category=ErrorCategory.RECOVERABLE,
        reason=f"{type(error).__name__} may be transient",
    )


SleepCallable = Callable[[float], Awaitable[None] | None]


class RetryController:
    def __init__(
        self,
        config: RetryConfig | None = None,
        sleep: SleepCallable | None = None,
    ) -> None:
        self.config = config or RetryConfig()
        self.sleep = sleep or asyncio.sleep
        self.attempt_log: list[RetryAttempt] = []

    async def run(
        self,
        operation_fn: Callable[[], Awaitable[Any] | Any],
        *,
        operation: str,
    ) -> Any:
        for attempt in range(1, self.config.max_retries + 2):
            try:
                result = operation_fn()
                if isinstance(result, Awaitable):
                    result = await result
            except Exception as error:
                classification = classify_error(error)
                can_retry = (
                    classification.category is ErrorCategory.RECOVERABLE
                    and isinstance(error, self.config.retryable_exceptions)
                    and attempt <= self.config.max_retries
                )
                delay = self._delay_for_attempt(attempt) if can_retry else None
                self.attempt_log.append(
                    RetryAttempt(
                        attempt=attempt,
                        operation=operation,
                        success=False,
                        category=classification.category,
                        error_type=type(error).__name__,
                        error=str(error),
                        delay=delay,
                    )
                )
                if not can_retry:
                    if (
                        classification.category is ErrorCategory.RECOVERABLE
                        and isinstance(error, self.config.retryable_exceptions)
                    ):
                        raise RetryExhaustedError(self._build_summary(operation, error)) from error
                    raise
                await self._sleep(delay)
            else:
                self.attempt_log.append(
                    RetryAttempt(
                        attempt=attempt,
                        operation=operation,
                        success=True,
                    )
                )
                return result

        raise RuntimeError("unreachable retry state")

    def recovery_result(self, strategy: RecoveryStrategy, payload: Any = None) -> dict[str, Any]:
        return {
            "strategy": strategy.value,
            "payload": payload,
            "attempts": len(self.attempt_log),
        }

    def _delay_for_attempt(self, attempt: int) -> float:
        delay = self.config.base_delay * (self.config.backoff_factor ** (attempt - 1))
        if self.config.jitter:
            delay += random.uniform(0, self.config.jitter)
        return delay

    async def _sleep(self, delay: float) -> None:
        result = self.sleep(delay)
        if isinstance(result, Awaitable):
            await result

    def _build_summary(self, operation: str, error: BaseException) -> dict[str, Any]:
        return {
            "operation": operation,
            "strategy": self.config.exhausted_strategy.value,
            "attempts": len(self.attempt_log),
            "final_error_type": type(error).__name__,
            "final_error": str(error),
            "attempt_log": [
                {
                    "attempt": attempt.attempt,
                    "success": attempt.success,
                    "category": attempt.category.value if attempt.category else None,
                    "error_type": attempt.error_type,
                    "error": attempt.error,
                    "delay": attempt.delay,
                }
                for attempt in self.attempt_log
            ],
        }


__all__ = [
    "ErrorCategory",
    "ErrorClassification",
    "RecoveryStrategy",
    "RetryAttempt",
    "RetryConfig",
    "RetryController",
    "RetryExhaustedError",
    "classify_error",
]

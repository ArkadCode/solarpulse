"""Retries with exponential backoff and jitter."""

from __future__ import annotations

import functools
import random
import time
from collections.abc import Callable
from typing import TypeVar

from src.common.logging import get_logger

log = get_logger("retry")

T = TypeVar("T")


class RetryableError(Exception):
    """Transient error (network, 429, 5xx). `retry_after` comes from the Retry-After header."""

    def __init__(self, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


def compute_delay(attempt: int, base: float, cap: float, retry_after: float | None = None) -> float:
    """Wait time for attempt number `attempt` (starts at 1). Honors Retry-After when sent."""
    if retry_after is not None:
        return min(retry_after, cap)
    exp = min(cap, base * 2 ** (attempt - 1))
    return random.uniform(exp / 2, exp)  # jitter so retries don't synchronize


def retry_with_backoff(
    max_attempts: int = 5,
    base_delay: float = 2.0,
    max_delay: float = 60.0,
    sleep: Callable[[float], None] = time.sleep,
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> T:
            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except RetryableError as exc:
                    if attempt == max_attempts:
                        log.error(
                            "retries_exhausted",
                            extra={"func": func.__name__, "attempts": attempt, "error": str(exc)},
                        )
                        raise
                    delay = compute_delay(attempt, base_delay, max_delay, exc.retry_after)
                    log.warning(
                        "retrying",
                        extra={
                            "func": func.__name__,
                            "attempt": attempt,
                            "delay_s": round(delay, 2),
                            "error": str(exc),
                        },
                    )
                    sleep(delay)
            raise AssertionError("unreachable")

        return wrapper

    return decorator

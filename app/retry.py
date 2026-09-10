"""Minimal exponential-backoff retry decorator (no third-party dependency)."""
from __future__ import annotations

import functools
import logging
import random
import time
from typing import Callable, TypeVar

log = logging.getLogger(__name__)
T = TypeVar("T")


def retry(attempts: int = 4, base_delay: float = 2.0, max_delay: float = 30.0,
          exceptions: tuple[type[BaseException], ...] = (Exception,),
          jitter: float = 0.3) -> Callable:
    """Retry `attempts` times with exponential backoff and jitter, then re-raise."""
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> T:
            delay = base_delay
            for attempt in range(1, attempts + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as exc:
                    if attempt == attempts:
                        log.error("%s failed after %d attempt(s): %s", func.__name__, attempt, exc)
                        raise
                    sleep_for = min(delay, max_delay) * (1 + random.uniform(-jitter, jitter))
                    log.warning("%s attempt %d/%d failed (%s), retrying in %.1fs",
                                func.__name__, attempt, attempts, exc, sleep_for)
                    time.sleep(sleep_for)
                    delay *= 2
            raise RuntimeError("unreachable")
        return wrapper
    return decorator

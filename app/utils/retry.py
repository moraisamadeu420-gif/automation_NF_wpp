"""
app/utils/retry.py
Synchronous retry decorator with exponential back-off.
"""
import time
from collections.abc import Callable
from functools import wraps
from typing import TypeVar

from loguru import logger

F = TypeVar("F", bound=Callable)


def retry(attempts: int = 3, delay: float = 2.0, exceptions: tuple = (Exception,)):
    """Retry a synchronous callable on failure with linear back-off."""
    def decorator(func: F) -> F:
        @wraps(func)
        def wrapper(*args, **kwargs):
            last_exc: Exception | None = None
            for attempt in range(1, attempts + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as exc:
                    last_exc = exc
                    if attempt < attempts:
                        wait = delay * attempt
                        logger.warning(
                            "Attempt {}/{} failed for '{}': {}. Retrying in {:.1f}s",
                            attempt, attempts, func.__name__, exc, wait,
                        )
                        time.sleep(wait)
                    else:
                        logger.error(
                            "All {} attempts failed for '{}'", attempts, func.__name__
                        )
            raise last_exc  # type: ignore[misc]
        return wrapper  # type: ignore[return-value]
    return decorator

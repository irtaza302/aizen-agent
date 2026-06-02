"""
Retry logic with exponential backoff for transient API errors.
"""

import time
import functools
from rich.text import Text

from .config import console
from .exceptions import RateLimitError, APIConnectionError


def retry_with_backoff(
    max_retries: int = 3,
    backoff_base: float = 2.0,
    retryable_exceptions: tuple | None = None,
):
    """
    Decorator that retries a function on transient failures with exponential backoff.

    Args:
        max_retries: Maximum number of retry attempts.
        backoff_base: Base for exponential backoff (delay = base ** attempt).
        retryable_exceptions: Tuple of exception types to retry on.
    """
    if retryable_exceptions is None:
        retryable_exceptions = (RateLimitError, APIConnectionError)

    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            last_exception: BaseException = RuntimeError("Retry exhausted")
            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except retryable_exceptions as e:
                    last_exception = e
                    if attempt < max_retries:
                        delay = backoff_base ** attempt
                        retry_msg = Text()
                        retry_msg.append("  ⏳ ", style="yellow")
                        retry_msg.append(
                            f"{type(e).__name__}: {e}. ",
                            style="dim",
                        )
                        retry_msg.append(
                            f"Retrying in {delay:.0f}s... ({attempt + 1}/{max_retries})",
                            style="dim italic",
                        )
                        console.print(retry_msg)
                        time.sleep(delay)
                    else:
                        raise last_exception
            raise last_exception  # Should not reach here, but safety net

        return wrapper

    return decorator

"""
Retry logic with exponential backoff + jitter for transient API errors.

Supports both synchronous and asynchronous functions — the decorator
auto-detects coroutine functions and uses asyncio.sleep accordingly.
"""

import asyncio
import functools
import inspect
import random
import time

from rich.text import Text

from .config import console


def _compute_delay(backoff_base: float, attempt: int, jitter: bool) -> float:
    """Calculate retry delay with optional jitter."""
    delay = backoff_base**attempt
    if jitter:
        delay *= 1.0 + random.uniform(-0.25, 0.25)
    return delay


def _print_retry_message(
    exception: BaseException, delay: float, attempt: int, max_retries: int
) -> None:
    """Print a formatted retry notice to the console."""
    retry_msg = Text()
    retry_msg.append("  ⏳ ", style="yellow")
    retry_msg.append(f"{type(exception).__name__}. ", style="dim")
    retry_msg.append(
        f"Retrying in {delay:.1f}s... ({attempt + 1}/{max_retries})",
        style="dim italic",
    )
    console.print(retry_msg)


def _is_retryable_503(e: BaseException) -> bool:
    """Check if an exception represents a 503 Service Unavailable."""
    return hasattr(e, "status_code") and e.status_code == 503


def retry_with_backoff(
    max_retries: int = 3,
    backoff_base: float = 2.0,
    retryable_exceptions: tuple | None = None,
    jitter: bool = True,
):
    """
    Decorator that retries a function on transient failures with exponential backoff.

    Automatically detects async functions and uses ``asyncio.sleep`` instead of
    ``time.sleep`` so that the event loop is never blocked.

    Args:
        max_retries: Maximum number of retry attempts.
        backoff_base: Base for exponential backoff (delay = base ** attempt).
        retryable_exceptions: Tuple of exception types to retry on.
        jitter: If True, adds random jitter (±25%) to prevent thundering herd.
    """
    if retryable_exceptions is None:
        # Import here to avoid circular imports — these are the standard transient errors
        from openai import (
            APIConnectionError as OpenAIConnectionError,
        )
        from openai import (
            APITimeoutError,
        )
        from openai import (
            RateLimitError as OpenAIRateLimitError,
        )

        retryable_exceptions = (
            OpenAIRateLimitError,
            APITimeoutError,
            OpenAIConnectionError,
        )

    def decorator(func):
        # ── Async wrapper ──
        if inspect.iscoroutinefunction(func):

            @functools.wraps(func)
            async def async_wrapper(*args, **kwargs):
                last_exception: BaseException = RuntimeError("Retry exhausted")
                for attempt in range(max_retries + 1):
                    try:
                        return await func(*args, **kwargs)
                    except retryable_exceptions as e:
                        last_exception = e
                        if attempt < max_retries:
                            delay = _compute_delay(backoff_base, attempt, jitter)
                            _print_retry_message(e, delay, attempt, max_retries)
                            await asyncio.sleep(delay)
                    except Exception as e:
                        if _is_retryable_503(e):
                            last_exception = e
                            if attempt < max_retries:
                                delay = _compute_delay(backoff_base, attempt, jitter)
                                _print_retry_message(e, delay, attempt, max_retries)
                                await asyncio.sleep(delay)
                                continue
                        raise
                raise last_exception

            return async_wrapper

        # ── Sync wrapper ──
        @functools.wraps(func)
        def sync_wrapper(*args, **kwargs):
            last_exception: BaseException = RuntimeError("Retry exhausted")
            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except retryable_exceptions as e:
                    last_exception = e
                    if attempt < max_retries:
                        delay = _compute_delay(backoff_base, attempt, jitter)
                        _print_retry_message(e, delay, attempt, max_retries)
                        time.sleep(delay)
                except Exception as e:
                    if _is_retryable_503(e):
                        last_exception = e
                        if attempt < max_retries:
                            delay = _compute_delay(backoff_base, attempt, jitter)
                            _print_retry_message(e, delay, attempt, max_retries)
                            time.sleep(delay)
                            continue
                    raise
            raise last_exception

        return sync_wrapper

    return decorator

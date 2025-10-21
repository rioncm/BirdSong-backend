from __future__ import annotations

import random
import time
from typing import Callable, Iterable, Optional, Tuple, Type, TypeVar

T = TypeVar("T")


def with_retry(
    operation: Callable[[], T],
    *,
    attempts: int = 3,
    base_delay: float = 0.5,
    max_delay: float = 5.0,
    exceptions: Tuple[Type[BaseException], ...] = (Exception,),
    logger=None,
    description: Optional[str] = None,
    jitter: float = 0.2,
    is_retryable: Optional[Callable[[BaseException], bool]] = None,
) -> T:
    """
    Execute the callable with exponential backoff and jitter.

    Args:
        operation: Callable to execute.
        attempts: Maximum number of attempts.
        base_delay: Initial delay between attempts (seconds).
        max_delay: Maximum delay between retries (seconds).
        exceptions: Exception types that should trigger retry logic.
        logger: Optional logger for structured logging.
        description: Human-readable description for logging.
        jitter: Fractional jitter to apply to each delay (0.2 => ±20%).
        is_retryable: Optional predicate to determine whether a caught exception
            should trigger a retry. Defaults to checking an attribute named
            ``retryable`` on the exception (if present).
    """
    delay = base_delay
    desc = description or getattr(operation, "__name__", "operation")

    for attempt in range(1, max(1, attempts) + 1):
        try:
            return operation()
        except exceptions as exc:  # type: ignore[misc]
            retryable = True
            if is_retryable is not None:
                retryable = bool(is_retryable(exc))
            elif hasattr(exc, "retryable"):
                retryable = bool(getattr(exc, "retryable"))

            if not retryable or attempt == attempts:
                raise

            if logger is not None:
                logger.warning(
                    "Retrying %s after %s (attempt %s/%s)",
                    desc,
                    exc,
                    attempt,
                    attempts,
                )

            jitter_factor = 1.0
            if jitter > 0:
                jitter_factor = random.uniform(1 - jitter, 1 + jitter)
            time.sleep(delay * jitter_factor)
            delay = min(max_delay, delay * 2)

    # Should be unreachable because loop either returns or raises
    raise RuntimeError(f"Retry loop for {desc} exited unexpectedly")

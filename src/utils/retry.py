import functools
import random
import time
from typing import Any, Callable, Tuple, Type

from src.config import BASE_RETRY_DELAY, MAX_RETRIES, MAX_RETRY_DELAY
from src.logger import get_logger

logger = get_logger(__name__)


def exponential_backoff_retry(
    max_retries: int = MAX_RETRIES,
    base_delay: float = BASE_RETRY_DELAY,
    max_delay: float = MAX_RETRY_DELAY,
    exceptions: Tuple[Type[Exception], ...] = (Exception,),
    jitter: bool = True,
):
    """
    Decorator that implements exponential backoff retry logic.

    Args:
        max_retries: Maximum number of retry attempts
        base_delay: Initial delay between retries in seconds
        max_delay: Maximum delay between retries in seconds
        exceptions: Tuple of exception types to retry on
        jitter: Whether to add random jitter to prevent thundering herd
    """

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            last_exception = None

            for attempt, _ in enumerate(range(max_retries), start=1):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e

                    if attempt == max_retries:
                        logger.error(
                            f"Function {func.__name__} failed after {max_retries} retries",
                            extra={
                                "function": func.__name__,
                                "attempt": attempt,
                                "max_retries": max_retries,
                                "exception": str(e),
                            },
                        )
                        raise e

                    # Calculate exponential backoff delay
                    delay = min(base_delay * (2**attempt), max_delay)

                    # Add jitter to prevent thundering herd
                    if jitter:
                        delay = delay * (0.5 + random.random() * 0.5)

                    logger.warning(
                        f"Function {func.__name__} failed on attempt {attempt}, retrying in {delay:.2f}s",
                        extra={
                            "function": func.__name__,
                            "attempt": attempt,
                            "max_retries": max_retries,
                            "delay": delay,
                            "exception": str(e),
                        },
                    )

                    time.sleep(delay)
                except Exception as e:
                    # For non-retryable exceptions, raise immediately
                    logger.error(
                        f"Function {func.__name__} failed with non-retryable exception",
                        extra={
                            "function": func.__name__,
                            "exception": str(e),
                            "exception_type": type(e).__name__,
                        },
                    )
                    raise e

            # This should never be reached, but just in case
            if last_exception:
                raise last_exception

        return wrapper

    return decorator


# def retry_with_backoff(func: Callable, *args, **kwargs) -> Any:
#     """
#     Standalone function to retry a function with exponential backoff.
#     Useful when the decorator can't be used.
#     """
#     @exponential_backoff_retry()
#     def _wrapped():
#         return func(*args, **kwargs)

#     return _wrapped()

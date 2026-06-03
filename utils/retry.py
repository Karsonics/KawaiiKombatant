import time
from functools import wraps
from typing import Callable, Optional, Type, Tuple


def retry(
    max_attempts: int = 3,
    delay: float = 2,
    backoff: float = 2,
    exceptions: Optional[Tuple[Type[Exception], ...]] = None,
):
    if exceptions is None:
        exceptions = (Exception,)

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            from utils.logging import logger
            last_exc = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    last_exc = e
                    if attempt == max_attempts:
                        logger.error(
                            "%s failed after %d attempts: %s",
                            func.__name__, max_attempts, e,
                        )
                        raise
                    logger.warning(
                        "%s failed (attempt %d/%d): %s. Retrying in %.1fs...",
                        func.__name__, attempt, max_attempts, e,
                        delay * (backoff ** (attempt - 1)),
                    )
                    time.sleep(delay * (backoff ** (attempt - 1)))
            return None
        return wrapper
    return decorator

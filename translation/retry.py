"""Centralized retry/backoff. One place controls how many times, and how
long we wait between attempts, for a single provider call."""
import asyncio
import logging
import typing as t

from .errors import PermanentTranslationError

logger = logging.getLogger(__name__)

DEFAULT_RETRY_DELAYS: t.List[float] = [2, 4, 7]


async def run_with_retries(
    fn: t.Callable[[], t.Awaitable[t.Any]],
    *,
    delays: t.Optional[t.List[float]] = None,
    on_failure: t.Optional[t.Callable[[int, Exception], None]] = None,
) -> t.Any:
    """Call the zero-arg async callable `fn`, retrying transient failures
    using `delays` as the backoff schedule between attempts.

    A PermanentTranslationError is never retried -- retrying an empty
    response or a missing dependency just wastes the delay budget for no
    benefit.
    """
    delays = delays if delays is not None else DEFAULT_RETRY_DELAYS
    last_exc: t.Optional[Exception] = None

    for attempt in range(len(delays) + 1):
        try:
            return await fn()
        except PermanentTranslationError:
            raise
        except Exception as exc:  # noqa: BLE001 - deliberately broad, classified by caller
            last_exc = exc
            if on_failure is not None:
                try:
                    on_failure(attempt, exc)
                except Exception:  # pragma: no cover - logging must never break retry
                    logger.debug("on_failure callback raised", exc_info=True)
            if attempt >= len(delays):
                break
            await asyncio.sleep(delays[attempt])

    raise last_exc or RuntimeError("translation failed after retries")

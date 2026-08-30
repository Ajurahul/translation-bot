"""Bot-wide cap on simultaneously-running translation jobs.

Each translation job spins up its own ThreadPoolExecutor (4-6 worker
threads, see utils/translate.py's Translator.get_no_of_workers) plus
whatever CPU/network load its chunks generate. On a resource-constrained
host (a small EC2 instance, for example), enough *simultaneous* jobs from
different Discord users can add up to far more OS threads and outbound
requests than the box can comfortably handle at once -- independent of,
and in addition to, the per-provider request/concurrency limits in
translation/manager.py and translation/ratelimit.py, which only bound
load *per provider*, not the total number of jobs running at once.

This is a simple counting semaphore: once `max_concurrent_jobs` jobs are
running, any further job waits for a slot rather than starting
immediately (and adding yet more threads on top of an already-loaded
box). It intentionally queues rather than rejects -- every requested
translation still happens, just not all at once.
"""
import asyncio
import logging
import typing as t

from .config import TranslationSettings
from .config import settings as global_settings

logger = logging.getLogger(__name__)


class JobSlotLimiter:
    def __init__(self, settings: t.Optional[TranslationSettings] = None) -> None:
        self._settings = settings or global_settings
        self._semaphore: t.Optional[asyncio.Semaphore] = None
        self._semaphore_limit: t.Optional[int] = None
        self._lock = asyncio.Lock()

    async def _get_semaphore(self) -> asyncio.Semaphore:
        limit = max(1, int(self._settings.max_concurrent_jobs))
        if self._semaphore is not None and self._semaphore_limit == limit:
            return self._semaphore
        async with self._lock:
            if self._semaphore is None or self._semaphore_limit != limit:
                self._semaphore = asyncio.Semaphore(limit)
                self._semaphore_limit = limit
        return self._semaphore

    def waiting_estimate(self) -> int:
        """Best-effort count of jobs currently queued for a slot (0 if
        none, or if the limiter hasn't been used yet). Only meaningful
        on the semaphore's own event loop."""
        if self._semaphore is None:
            return 0
        waiters = getattr(self._semaphore, "_waiters", None)
        return len(waiters) if waiters else 0

    class _Slot:
        def __init__(self, semaphore: asyncio.Semaphore) -> None:
            self._semaphore = semaphore

        async def __aenter__(self) -> "JobSlotLimiter._Slot":
            await self._semaphore.acquire()
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            self._semaphore.release()

    async def acquire(self) -> "JobSlotLimiter._Slot":
        """Returns an async context manager: `async with await limiter.acquire():`.
        Must be called from the event loop the job itself runs on --
        translation jobs in this project are always driven from the
        bot's main loop (see Translator.start in utils/translate.py),
        so a single asyncio.Semaphore is safe here (unlike the
        per-provider concurrency bounding in manager.py, which has to
        support genuinely different OS threads/loops)."""
        semaphore = await self._get_semaphore()
        return JobSlotLimiter._Slot(semaphore)


job_limiter = JobSlotLimiter()

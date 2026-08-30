"""Per-job translation manager.

One TranslationManager instance == one translation job. It resolves the
requested `engine` selector ("default" / "auto" / an explicit provider
id) exactly once at construction time, then owns all provider selection,
retry, health tracking, and response validation for that job.

Core behavioral rules (see docs/TRANSLATION_ENGINES.md for the full
write-up):
  * Explicit engine  -> only that engine is ever tried. No fallback.
  * Default          -> resolved from translation.config.settings once,
                         at job start; later admin changes don't affect
                         an already-running job.
  * Auto             -> tries the configured order, remembers whichever
                         engine first succeeds (`active_engine`) and
                         reuses it for later calls in the same job.
                         A `failed_engines` set is per-job only -- it is
                         never written back to global/shared state.
"""
import asyncio
import logging
import threading
import typing as t

from .config import TranslationSettings
from .config import settings as global_settings
from .errors import (
    AllEnginesFailedError,
    InvalidEngineError,
    TranslationFailedError,
)
from .registry import ProviderRegistry
from .registry import registry as global_registry
from .retry import run_with_retries
from .validation import validate_translation, validate_translation_batch

logger = logging.getLogger(__name__)

MODE_DEFAULT = "default"
MODE_AUTO = "auto"
MODE_EXPLICIT = "explicit"

# Bounds how many times Auto is allowed to clear its per-job failed-engine
# set and start a fresh pass once every candidate has failed. Keeps the
# "all engines failed -> reset -> retry" recovery in section 8/50 of the
# spec bounded rather than looping forever.
MAX_ALL_FAILED_RESETS = 1


class JobState:
    __slots__ = ("mode", "explicit_engine", "resolved_default_engine", "active_engine",
                 "failed_engines", "all_failed_resets")

    def __init__(self, mode: str, explicit_engine: t.Optional[str] = None) -> None:
        self.mode = mode
        self.explicit_engine = explicit_engine
        self.resolved_default_engine: t.Optional[str] = None
        self.active_engine: t.Optional[str] = None
        self.failed_engines: t.Set[str] = set()
        self.all_failed_resets = 0


class TranslationManager:
    """Concurrency across an entire process is bounded per-provider (a
    class-level semaphore keyed by engine id) rather than per-job, since
    the Discord bot already fans a single job's chunks out across worker
    threads (see utils/translate.py) -- what actually needs bounding is
    "how many requests are in flight against provider X at once",
    regardless of which job they belong to."""

    _provider_semaphores: t.Dict[str, threading.BoundedSemaphore] = {}
    _provider_semaphores_lock = threading.Lock()

    def __init__(
        self,
        engine: str = MODE_DEFAULT,
        settings: t.Optional[TranslationSettings] = None,
        registry: t.Optional[ProviderRegistry] = None,
    ) -> None:
        self._settings = settings or global_settings
        self._registry = registry or global_registry

        mode, explicit_engine = self._parse_engine_selector(engine)
        self.state = JobState(mode=mode, explicit_engine=explicit_engine)
        if mode == MODE_DEFAULT:
            self.state.resolved_default_engine = self._resolve_default_engine()

    # -- selector parsing / default resolution ------------------------
    @staticmethod
    def _parse_engine_selector(engine: t.Optional[str]) -> t.Tuple[str, t.Optional[str]]:
        value = (engine or MODE_DEFAULT).strip()
        low = value.lower()
        if low in (MODE_DEFAULT, ""):
            return MODE_DEFAULT, None
        if low == MODE_AUTO:
            return MODE_AUTO, None
        return MODE_EXPLICIT, low

    def _resolve_default_engine(self) -> str:
        candidate = self._settings.default_engine
        if candidate and self._registry.is_provider_available(candidate):
            return candidate
        # Persisted default is missing/corrupted/points at an unavailable
        # provider -- fail safe instead of crashing the job.
        logger.warning(
            "Persisted default engine %r unavailable, falling back", candidate
        )
        for fallback in ["googletrans", *self._settings.auto_engine_order]:
            if self._registry.is_provider_available(fallback):
                return fallback
        raise AllEnginesFailedError("No translation engines are currently available")

    @property
    def mode(self) -> str:
        return self.state.mode

    @property
    def display_mode(self) -> str:
        return {MODE_DEFAULT: "Default", MODE_AUTO: "Auto", MODE_EXPLICIT: "Explicit"}[self.state.mode]

    def resolved_engine_name(self) -> t.Optional[str]:
        """Best-effort "what engine is/will this job use" for status
        messages. For Auto this is None until the first successful call."""
        if self.state.mode == MODE_EXPLICIT:
            return self.state.explicit_engine
        if self.state.mode == MODE_DEFAULT:
            return self.state.resolved_default_engine
        return self.state.active_engine

    def display_engine_name(self) -> str:
        name = self.resolved_engine_name()
        if name is None:
            return "selecting..."
        return self._registry.get_display_name(name)

    # -- concurrency ----------------------------------------------------
    def _semaphore_for(self, engine_name: str) -> threading.BoundedSemaphore:
        limit = self._settings.provider_concurrency.get(engine_name, self._settings.max_concurrency)
        limit = max(1, int(limit))
        with TranslationManager._provider_semaphores_lock:
            sem = TranslationManager._provider_semaphores.get(engine_name)
            if sem is None:
                sem = threading.BoundedSemaphore(limit)
                TranslationManager._provider_semaphores[engine_name] = sem
        return sem

    async def _call_backend(self, engine_name: str, coro_factory) -> t.Any:
        backend = self._registry.get_provider(engine_name)
        sem = self._semaphore_for(engine_name)
        # threading.Semaphore.acquire() blocks synchronously -- calling it
        # directly here would stall the *entire* event loop (and every
        # other coroutine sharing it) whenever the permit isn't
        # immediately available, which can deadlock if the coroutine
        # that would eventually release it is scheduled on that same
        # loop. Routing the wait through asyncio.to_thread keeps the
        # blocking part on a worker thread so the loop stays free to run
        # other tasks (including the ones holding a permit) in the
        # meantime. This matters for both usage patterns in this project:
        # many chunks each in their own thread+loop (bounded_concurrency
        # is then effectively "how many threads are in flight", capped
        # process-wide per provider) and any future caller that awaits
        # several translations concurrently on a single event loop.
        await asyncio.to_thread(sem.acquire)
        try:
            return await coro_factory(backend)
        finally:
            sem.release()

    async def _try_engine_with_retry(self, engine_name: str, coro_factory, validator) -> t.Any:
        async def _attempt() -> t.Any:
            raw = await self._call_backend(engine_name, coro_factory)
            return validator(raw)

        return await run_with_retries(
            _attempt,
            delays=self._settings.retry_delays,
            on_failure=lambda attempt, exc: logger.info(
                "Provider failure provider=%s attempt=%s reason=%s", engine_name, attempt, exc
            ),
        )

    # -- public API -------------------------------------------------
    async def translate(self, text: str, source_language: str, target_language: str) -> str:
        return await self._run(
            coro_factory=lambda backend: backend.translate(text, source_language, target_language),
            validator=lambda raw: validate_translation(text, raw),
        )

    async def translate_batch(
        self, texts: t.List[str], source_language: str, target_language: str
    ) -> t.List[str]:
        return await self._run(
            coro_factory=lambda backend: backend.translate_batch(
                texts, source_language, target_language
            ),
            validator=lambda raw: validate_translation_batch(texts, raw),
        )

    def mark_success(self, engine: str) -> None:
        self.state.active_engine = engine
        self.state.failed_engines.discard(engine)

    def mark_failed(self, engine: str) -> None:
        self.state.failed_engines.add(engine)
        if self.state.active_engine == engine:
            self.state.active_engine = None

    def get_available_engines(self) -> t.List[str]:
        return self._registry.get_available_providers()

    # -- mode dispatch ------------------------------------------------
    async def _run(self, coro_factory, validator) -> t.Any:
        if self.state.mode == MODE_EXPLICIT:
            return await self._run_explicit(coro_factory, validator)
        if self.state.mode == MODE_DEFAULT:
            return await self._run_default(coro_factory, validator)
        return await self._run_auto(coro_factory, validator)

    async def _run_explicit(self, coro_factory, validator) -> t.Any:
        engine = self.state.explicit_engine
        if not self._registry.is_provider_available(engine):
            raise InvalidEngineError(f"Translation engine '{engine}' is not available")
        try:
            result = await self._try_engine_with_retry(engine, coro_factory, validator)
            self.mark_success(engine)
            return result
        except Exception as exc:
            raise TranslationFailedError(
                f"Translation failed using {self._registry.get_display_name(engine)}"
            ) from exc

    async def _run_default(self, coro_factory, validator) -> t.Any:
        engine = self.state.resolved_default_engine
        try:
            result = await self._try_engine_with_retry(engine, coro_factory, validator)
            self.mark_success(engine)
            return result
        except Exception as exc:
            raise TranslationFailedError(
                f"Translation failed using {self._registry.get_display_name(engine)}"
            ) from exc

    def _auto_candidates(self) -> t.List[str]:
        order = self._settings.auto_engine_order or self._registry.get_available_providers()
        healthy = [e for e in order if e not in self.state.failed_engines]
        return [e for e in healthy if self._registry.is_provider_available(e)]

    async def _run_auto(self, coro_factory, validator) -> t.Any:
        # Reuse the already-proven-healthy engine first -- this is the
        # whole point of Auto: find a working provider once, then stop
        # probing every engine on every subsequent chunk.
        if self.state.active_engine and self.state.active_engine not in self.state.failed_engines:
            engine = self.state.active_engine
            try:
                return await self._try_engine_with_retry(engine, coro_factory, validator)
            except Exception as exc:
                logger.info("Provider switched from=%s reason=%s", engine, exc)
                self.mark_failed(engine)

        last_exc: t.Optional[Exception] = None
        for _pass in range(MAX_ALL_FAILED_RESETS + 1):
            candidates = self._auto_candidates()
            if not candidates:
                if self.state.all_failed_resets >= MAX_ALL_FAILED_RESETS:
                    break
                self.state.all_failed_resets += 1
                self.state.failed_engines.clear()
                continue

            for engine in candidates:
                try:
                    result = await self._try_engine_with_retry(engine, coro_factory, validator)
                    self.mark_success(engine)
                    logger.info("Provider selected provider=%s mode=auto", engine)
                    return result
                except Exception as exc:
                    last_exc = exc
                    self.mark_failed(engine)
                    logger.info("Provider failure provider=%s reason=%s", engine, exc)

            # Every candidate in this pass failed. Bounded reset: clear
            # per-job failure state once and try a fresh pass, then stop.
            if self.state.all_failed_resets >= MAX_ALL_FAILED_RESETS:
                break
            self.state.all_failed_resets += 1
            self.state.failed_engines.clear()

        raise AllEnginesFailedError(
            "All available translation engines are currently unavailable"
        ) from last_exc

    async def aclose(self) -> None:
        """No per-job resources to release -- provider backends are
        process-lifetime singletons owned by the registry."""
        return None

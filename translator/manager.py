"""TranslationManager: resolves `Default` / `Auto` / an explicit engine
name into actual translation calls, per Rules 1-5.

One instance == one translation job. Engine health (which engines have
recently failed, and which engine Auto is currently "stuck" on) is kept
on the instance, not as class/module-level state, so one user's failing
engine never affects another user's concurrent job (section 12).
"""
import asyncio
import logging
import os
import threading
import typing as t

from translator import registry
from translator.base import TranslationBackend, TranslationFailedError, classify_exception
from translator.errors import validate_and_clean
from translator.settings import get_default_engine

logger = logging.getLogger("raizel_bot.translator")

AUTO = "auto"
DEFAULT = "default"

DEFAULT_MAX_CONCURRENCY = int(os.getenv("TRANSLATION_MAX_CONCURRENCY", "3"))
DEFAULT_MAX_RETRIES = int(os.getenv("TRANSLATION_MAX_RETRIES", "3"))
DEFAULT_RETRY_DELAYS = [
    int(x) for x in os.getenv("TRANSLATION_RETRY_DELAYS", "2,4,7").split(",") if x.strip()
]
DEFAULT_REQUEST_DELAY = float(os.getenv("TRANSLATION_REQUEST_DELAY", "0.2"))
DEFAULT_ENGINE_CALL_TIMEOUT = float(os.getenv("TRANSLATION_ENGINE_TIMEOUT", "20"))
DEFAULT_MIN_RECOVERABLE_CHUNK_CHARS = int(os.getenv("TRANSLATION_MIN_RECOVERABLE_CHUNK_CHARS", "200"))
DEFAULT_MAX_ENGINE_RESETS = int(os.getenv("TRANSLATION_MAX_ENGINE_RESETS", "2"))


class EngineChoice:
    """Resolves the value coming out of the Discord `translation_engine`
    option into what the manager actually needs to do."""

    def __init__(self, raw: t.Optional[str]) -> None:
        normalized = (raw or DEFAULT).strip().lower()
        self.is_auto = normalized == AUTO
        if self.is_auto:
            self.engine_key: t.Optional[str] = None
            self.resolved_from_default = False
            return
        if normalized == DEFAULT:
            self.engine_key = get_default_engine()
            self.resolved_from_default = True
        else:
            self.engine_key = normalized
            self.resolved_from_default = False


class TranslationManager:
    def __init__(
            self,
            engine_mode: t.Optional[str] = DEFAULT,
            max_concurrency: int = DEFAULT_MAX_CONCURRENCY,
            max_retries: int = DEFAULT_MAX_RETRIES,
            retry_delays: t.Optional[t.List[int]] = None,
            request_delay: float = DEFAULT_REQUEST_DELAY,
            engine_call_timeout: float = DEFAULT_ENGINE_CALL_TIMEOUT,
            min_recoverable_chunk_chars: int = DEFAULT_MIN_RECOVERABLE_CHUNK_CHARS,
            max_engine_resets: int = DEFAULT_MAX_ENGINE_RESETS,
            on_engine_switch: t.Optional[t.Callable[[str, str], None]] = None,
    ) -> None:
        self.choice = EngineChoice(engine_mode)
        self.max_concurrency = max_concurrency
        self.max_retries = max_retries
        self.retry_delays = retry_delays or list(DEFAULT_RETRY_DELAYS)
        self.request_delay = request_delay
        self.engine_call_timeout = engine_call_timeout
        self.min_recoverable_chunk_chars = min_recoverable_chunk_chars
        self.max_engine_resets = max_engine_resets
        self.on_engine_switch = on_engine_switch

        # NOTE: deliberately not storing an asyncio.Semaphore/Lock here --
        # a manager instance can be driven from multiple OS threads, each
        # running its own short-lived event loop (see
        # utils.translate.Translator, which submits chapters to a
        # ThreadPoolExecutor and does `asyncio.run(...)` per chapter on
        # worker threads). An asyncio primitive created in one loop can't
        # safely be reused from another, so `translate_many` creates its
        # semaphore fresh each call instead. The engine-health state below
        # (which engine is "sticky", which have failed) *does* need to
        # persist across those calls/threads for a single job, so it's
        # guarded by a plain threading.Lock instead.
        self._backend_cache: t.Dict[str, TranslationBackend] = {}
        self._working_engine: t.Optional[str] = None
        self._failed_engines: t.Set[str] = set()
        self._reset_count = 0
        self._state_lock = threading.Lock()

        # BUG FIX (found in review): the integration in
        # utils.translate.Translator._translate_batch_with_retry calls
        # `translate_many` with exactly ONE chunk per call, from inside a
        # ThreadPoolExecutor that itself runs 4-6 chunks truly
        # concurrently (see Translator.get_no_of_workers). That means the
        # asyncio.Semaphore created fresh inside `translate_many` below
        # never actually has more than one task competing for it -- it
        # was a no-op there, while the *real* concurrency (4-6
        # simultaneous network calls) was governed entirely by the thread
        # pool, completely uncoupled from `max_concurrency`/
        # TRANSLATION_MAX_CONCURRENCY. A `threading.Semaphore` (unlike
        # asyncio.Semaphore, this is safe to acquire/release from
        # different OS threads) closes that gap by bounding concurrency
        # at `_call_engine`, the one choke point every translation
        # request passes through regardless of which of the two
        # execution models (asyncio-only, or thread-pool-of-event-loops)
        # is driving it.
        self._thread_semaphore = threading.Semaphore(max(1, max_concurrency))

    # -- public introspection (section 12's requested surface) -----------

    def mark_engine_failed(self, engine: str) -> None:
        with self._state_lock:
            self._failed_engines.add(engine)
            if self._working_engine == engine:
                self._working_engine = None

    def mark_engine_success(self, engine: str) -> t.Optional[str]:
        """Marks `engine` as the current working engine, atomically, and
        returns whatever the previous working engine was (or None) so
        callers can detect a genuine switch without a separate racy read
        of `_working_engine`."""
        with self._state_lock:
            self._failed_engines.discard(engine)
            previous = self._working_engine
            self._working_engine = engine
            return previous

    def get_available_engines(self) -> t.List[str]:
        with self._state_lock:
            failed = set(self._failed_engines)
        return [spec.key for spec in registry.available_specs() if spec.key not in failed]

    def reset_failed_engines(self) -> None:
        with self._state_lock:
            logger.info("Translation engines reset engines=%s", sorted(self._failed_engines))
            self._failed_engines.clear()

    @property
    def current_engine(self) -> t.Optional[str]:
        """Best-known "currently in use" engine, for UI status messages."""
        if not self.choice.is_auto:
            return self.choice.engine_key
        return self._working_engine

    # -- internals ---------------------------------------------------------

    def _get_backend(self, engine_key: str) -> TranslationBackend:
        with self._state_lock:
            backend = self._backend_cache.get(engine_key)
            if backend is None:
                backend = registry.get_backend(engine_key)
                self._backend_cache[engine_key] = backend
            return backend

    def _auto_engine_order(self) -> t.List[str]:
        available = [spec.key for spec in registry.available_specs()]
        if not available:
            return []
        with self._state_lock:
            working = self._working_engine
            failed = set(self._failed_engines)
        ordered: t.List[str] = []
        if working and working in available and working not in failed:
            ordered.append(working)
        for key in available:
            if key not in ordered and key not in failed:
                ordered.append(key)
        return ordered

    async def _call_engine(self, engine_key: str, text: str, source: str, target: str) -> str:
        backend = self._get_backend(engine_key)
        if self.request_delay:
            await asyncio.sleep(self.request_delay)
        # Blocking acquire, but run in a helper thread via
        # `asyncio.to_thread` so it never blocks *this* coroutine's event
        # loop while waiting for a slot -- important because this code
        # can run either on a throwaway per-chunk loop (fine either way)
        # or, for a direct async caller, on the bot's own main event loop
        # (where blocking synchronously would stall Discord's gateway
        # heartbeat and other unrelated tasks).
        await asyncio.to_thread(self._thread_semaphore.acquire)
        try:
            result = await asyncio.wait_for(
                backend.translate(text, source, target),
                timeout=self.engine_call_timeout,
            )
        finally:
            self._thread_semaphore.release()
        # Defense in depth: every backend is expected to validate its own
        # result (see translator/backends/*), but the manager validates
        # again here so a response-shaped failure (error page, empty,
        # None -- section 13) is always caught and fed back into the
        # normal retry/failure-tracking path, even for a backend that
        # forgot to check. This must never be treated as a success.
        from translator.base import RetryableTranslationError
        from translator.errors import validate_and_clean
        try:
            return validate_and_clean([result])[0]
        except RuntimeError as e:
            raise RetryableTranslationError(str(e)) from e

    async def _translate_single_explicit(self, text: str, source: str, target: str) -> str:
        engine_key = self.choice.engine_key
        spec = registry.get_spec(engine_key)
        if spec is None or not registry.is_engine_available(engine_key):
            display = spec.display_name if spec else engine_key
            raise TranslationFailedError(
                f"Translation failed using {display}. The selected engine is currently unavailable.",
                engine=engine_key,
            )

        last_exc: t.Optional[BaseException] = None
        for attempt in range(self.max_retries + 1):
            if attempt > 0:
                delay = self.retry_delays[min(attempt - 1, len(self.retry_delays) - 1)]
                await asyncio.sleep(delay)
            try:
                result = await self._call_engine(engine_key, text, source, target)
                logger.info("Translation engine succeeded engine=%s", engine_key)
                return result
            except Exception as e:
                last_exc = e
                retryable = classify_exception(e)
                logger.warning(
                    "Translation engine failed engine=%s reason=%s retryable=%s attempt=%s",
                    engine_key, e, retryable, attempt,
                )
                if not retryable:
                    break

        raise TranslationFailedError(
            f"Translation failed using {spec.display_name}. "
            f"The selected engine is currently unavailable.",
            engine=engine_key,
            cause=last_exc,
        )

    async def _translate_single_auto(self, text: str, source: str, target: str) -> str:
        last_exc: t.Optional[BaseException] = None
        for reset_round in range(self.max_engine_resets + 1):
            order = self._auto_engine_order()
            if not order:
                break
            for engine_key in order:
                try:
                    result = await self._call_engine(engine_key, text, source, target)
                except Exception as e:
                    last_exc = e
                    self.mark_engine_failed(engine_key)
                    logger.warning(
                        "Translation engine failed engine=%s reason=%s (auto mode)", engine_key, e,
                    )
                    continue

                # BUG FIX (found in review): reading self._working_engine
                # directly here was an unguarded access to state that
                # `mark_engine_failed`/`mark_engine_success` mutate under
                # `_state_lock` from other threads -- a real race in a
                # bot where several jobs (and, within one job, several
                # ThreadPoolExecutor workers) can call into this manager
                # concurrently. `mark_engine_success` now does the swap
                # atomically and hands back what the previous value was.
                previous = self.mark_engine_success(engine_key)
                if previous != engine_key:
                    logger.info("Translation engine switched from=%s to=%s", previous, engine_key)
                    if self.on_engine_switch and previous is not None:
                        self.on_engine_switch(previous, engine_key)
                return result

            # Every currently-known-available engine failed this round.
            if reset_round < self.max_engine_resets:
                self.reset_failed_engines()
                with self._state_lock:
                    self._reset_count += 1
            else:
                break

        raise TranslationFailedError(
            "All available translation engines are currently unavailable.",
            engine="auto",
            cause=last_exc,
        )

    async def _translate_single_with_policy(self, text: str, source: str, target: str) -> str:
        if self.choice.is_auto:
            return await self._translate_single_auto(text, source, target)
        return await self._translate_single_explicit(text, source, target)

    async def _translate_text_with_recovery(self, text: str, source: str, target: str, depth: int = 0) -> str:
        """Mirrors the legacy chunk-recovery behaviour (section 17): if a
        chunk can't be translated, split it and retry the halves, down to
        `min_recoverable_chunk_chars`, without recursing forever."""
        try:
            return await self._translate_single_with_policy(text, source, target)
        except TranslationFailedError:
            if len(text) <= self.min_recoverable_chunk_chars or depth >= 6:
                raise
            mid = len(text) // 2
            # Prefer splitting on a paragraph/sentence boundary near the
            # midpoint so we don't cut a sentence in half where avoidable.
            for boundary in ("\n\n", "\n", ". "):
                pos = text.rfind(boundary, 0, mid + len(boundary))
                if pos > 0:
                    mid = pos + len(boundary)
                    break
            first, second = text[:mid], text[mid:]

            async def _half(part: str) -> str:
                try:
                    return await self._translate_text_with_recovery(part, source, target, depth + 1)
                except TranslationFailedError:
                    return "\n\n--->couldn't translate this part\n" + part

            return (await _half(first)) + (await _half(second))

    # -- public API ----------------------------------------------------

    async def translate_one(self, text: str, source: str, target: str) -> str:
        result = await self._translate_text_with_recovery(text, source, target)
        return validate_and_clean([result])[0]

    async def translate_many(
            self,
            texts: t.List[str],
            source: str,
            target: str,
            progress_cb: t.Optional[t.Callable[[int, int], None]] = None,
    ) -> t.List[str]:
        """Translate a list of chunks with bounded concurrency
        (asyncio.Semaphore, section 15), preserving input order."""
        results: t.List[t.Optional[str]] = [None] * len(texts)
        completed = 0
        lock = asyncio.Lock()
        # Created fresh per call (and bound to whichever event loop is
        # running this coroutine) -- see the note in __init__.
        semaphore = asyncio.Semaphore(max(1, self.max_concurrency))

        async def _run(idx: int, chunk: str) -> None:
            nonlocal completed
            async with semaphore:
                results[idx] = await self.translate_one(chunk, source, target)
            if progress_cb:
                async with lock:
                    completed += 1
                    progress_cb(completed, len(texts))

        await asyncio.gather(*(_run(i, chunk) for i, chunk in enumerate(texts)))
        return t.cast(t.List[str], results)

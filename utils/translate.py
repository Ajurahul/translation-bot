import asyncio
import concurrent.futures
import threading
import time
import typing as t

from core.bot import Raizel
from languages import languages
from translation.detection import detect_language_code
from translation.config import settings as _translation_settings
from translation.errors import (
    AllEnginesFailedError,
    InvalidEngineError,
    TranslationError,
    is_none_like,
    is_provider_request_error,
)
from translation.errors import filter_error_text as _filter_error_text_impl
from translation.errors import is_error_response as _is_error_response_impl
from translation import health as _translation_health
from translation.providers.text_chunking import split_text
from translation.jobs import job_limiter
from translation.manager import TranslationManager
from translation.registry import registry as translation_registry


# Each translation worker thread keeps ONE event loop for its whole life
# (created by Translator.translates()'s executor initializer) instead of
# `asyncio.run()` building and tearing down a fresh loop for every chunk.
# A fresh loop per chunk meant a fresh httpx client / connection pool / TLS
# handshake per chunk, and made every per-loop cache in the providers
# useless. Threads that have no such loop (anything else calling the
# blocking helpers) fall back to asyncio.run() exactly as before.
_worker_local = threading.local()


class Translator:
    """Backward-compatible facade over the translation.* package.

    Historically this class contained all of the engine-specific
    translation logic (googletrans/deep-translator/bing switching,
    retries, error detection) inline. That logic now lives in
    translation/manager.py + translation/providers/*.py so it can be
    reused outside the Discord chunk-translation flow (e.g. by the admin
    command's availability checks) and unit tested in isolation.

    Every existing call site keeps working unchanged:
      - Translator(bot, user, language) / .translates() / .start()
      - Translator.translate_with_retry(...) / .atranslate_with_retry(...)
      - Translator.translate_batch_with_retry(...) / .atranslate_batch_with_retry(...)
      - Translator.detect_with_retry(...) / .adetect_with_retry(...)
      - Translator._is_error_500_response(...) / Translator._filter_error_text(...)

    New code can additionally pass `engine=` to select "default" (the
    admin-configured engine, persisted in config/translation_settings.json),
    "auto" (intelligent per-job failover), or a specific provider id from
    translation.registry (e.g. "translators-bing").

    Per-job engine-usage tracking: a single Discord `/translate` job may
    span *several* Translator instances (see cogs/translation.py's large-
    file path, which creates a fresh Translator per outer chunk group,
    each with its own TranslationManager), so usage is aggregated at the
    class level, keyed by Discord user id, rather than kept only on
    `self._manager.usage`:
      - `Translator.reset_job_usage(user_id)` at the start of a job.
      - each instance folds its own manager's usage in automatically at
        the end of `start()`.
      - `Translator.get_job_usage_summary(user_id)` for the human-
        readable summary shown in Discord embeds (e.g. "Google Translate"
        if one engine did everything, or "Google Translate (41), MyMemory
        (3)" if the job had to hop mid-way).
    """

    # user id -> {engine id -> chunks translated}. threading.Lock (not
    # asyncio.Lock): translates() fans work out across worker threads
    # (concurrent.futures.ThreadPoolExecutor), each running its own
    # asyncio.run() via _run_async_blocking, so this is genuinely
    # touched from multiple OS threads at once.
    _job_usage: t.Dict[int, t.Dict[str, int]] = {}
    _job_usage_lock = threading.Lock()

    def __init__(self, bot: Raizel, user: int, language: str, engine: str = "default") -> None:
        self.bot = bot
        self.user = user
        self.language = language
        self.order = {}
        self.engine = engine
        self._manager = TranslationManager(engine=engine)

    @property
    def manager(self) -> TranslationManager:
        return self._manager

    # -- per-job engine-usage tracking, aggregated across every
    # Translator/TranslationManager instance a single job touches -------
    @classmethod
    def reset_job_usage(cls, user_id: int) -> None:
        """Call once at the very start of a `/translate` job (before any
        Translator instance for it is constructed) so a previous job's
        usage for this user doesn't leak into the new one."""
        with cls._job_usage_lock:
            cls._job_usage[user_id] = {}

    def _fold_usage_into_job(self) -> None:
        usage = self._manager.usage
        if not usage:
            return
        with Translator._job_usage_lock:
            agg = Translator._job_usage.setdefault(self.user, {})
            for engine, count in usage.items():
                agg[engine] = agg.get(engine, 0) + count

    @classmethod
    def get_job_usage_summary(cls, user_id: int) -> str:
        """Human-readable "which engine(s) actually translated this"
        summary for Discord embeds. Falls back to "Auto (<label>)" (or
        just the resolved engine's label, for Default/explicit jobs) if
        nothing has been tracked yet -- e.g. shown while a job is still
        starting up, before its first chunk has completed."""
        with cls._job_usage_lock:
            usage = dict(cls._job_usage.get(user_id, {}))
        if not usage:
            return f"Auto ({TranslationManager().display_engine_name()})"
        if len(usage) == 1:
            engine = next(iter(usage))
            return translation_registry.get_display_name(engine)
        ranked = sorted(usage.items(), key=lambda kv: (-kv[1], kv[0]))
        return ", ".join(
            f"{translation_registry.get_display_name(engine)} ({count})"
            for engine, count in ranked
        )

    # -- response validation helpers (kept for backward compatibility) --
    @staticmethod
    def _is_error_500_response(translated: t.List[str]) -> bool:
        return _is_error_response_impl(translated)

    @staticmethod
    def _filter_error_text(text: str) -> str:
        return _filter_error_text_impl(text)

    @staticmethod
    def _normalize_language(value: str, fallback: str = "en") -> str:
        if not value:
            return fallback
        lang = str(value).strip().lower()
        if lang == "auto":
            return "auto"
        if lang in languages.choices:
            return str(languages.choices[lang]).lower()
        return lang

    @staticmethod
    def _run_async_blocking(async_fn, *args, **kwargs):
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            loop = getattr(_worker_local, "loop", None)
            if loop is not None and not loop.is_closed() and not loop.is_running():
                return loop.run_until_complete(async_fn(*args, **kwargs))
            return asyncio.run(async_fn(*args, **kwargs))

        # If called from an active event loop, run the async function in a helper thread.
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(lambda: asyncio.run(async_fn(*args, **kwargs)))
            return future.result()

    # -- one-off helpers (title/description translation etc). These are
    # not tied to any particular translation job, so they default to Auto
    # for resilience regardless of what engine a running job selected. --
    @staticmethod
    async def atranslate_with_retry(
            text: str,
            target: str = "english",
            source: str = "auto",
            retry_delays: t.Optional[t.List[int]] = None,
            engine: str = "auto",
    ) -> str:
        manager = TranslationManager(engine=engine)
        target_code = Translator._normalize_language(target, fallback="en")
        source_code = Translator._normalize_language(source, fallback="auto")
        try:
            return await manager.translate(str(text), source_code, target_code)
        except TranslationError as e:
            raise RuntimeError(str(e)) from e

    @staticmethod
    def translate_with_retry(
            text: str,
            target: str = "english",
            source: str = "auto",
            retry_delays: t.Optional[t.List[int]] = None,
            engine: str = "auto",
    ) -> str:
        return Translator._run_async_blocking(
            Translator.atranslate_with_retry, text, target, source, retry_delays, engine,
        )

    @staticmethod
    async def atranslate_batch_with_retry(
            chapter: t.List[str],
            target: str,
            source: str = "auto",
            retry_delays: t.Optional[t.List[int]] = None,
            engine: str = "auto",
    ) -> t.List[str]:
        manager = TranslationManager(engine=engine)
        target_code = Translator._normalize_language(target, fallback="en")
        source_code = Translator._normalize_language(source, fallback="auto")
        try:
            return await manager.translate_batch(list(chapter), source_code, target_code)
        except TranslationError as e:
            raise RuntimeError(str(e)) from e

    @staticmethod
    def translate_batch_with_retry(
            chapter: t.List[str],
            target: str,
            source: str = "auto",
            retry_delays: t.Optional[t.List[int]] = None,
            engine: str = "auto",
    ) -> t.List[str]:
        return Translator._run_async_blocking(
            Translator.atranslate_batch_with_retry, chapter, target, source, retry_delays, engine,
        )

    # -- instance-level batch call, routed through this job's manager so
    # Auto mode's "remember the healthy engine" behavior applies across
    # every chunk of this particular translation job --------------------
    async def _atranslate_batch_with_retry(self, chapter: t.List[str]) -> t.List[str]:
        target_code = Translator._normalize_language(self.language, fallback="en")
        try:
            return await self._manager.translate_batch(list(chapter), "auto", target_code)
        except TranslationError as e:
            raise RuntimeError(str(e)) from e

    def _translate_batch_with_retry(self, chapter: t.List[str]) -> t.List[str]:
        return Translator._run_async_blocking(self._atranslate_batch_with_retry, chapter)

    # -- crawled-title translation ----------------------------------------
    @staticmethod
    def _usable_title(original: str, result: t.Any) -> t.Optional[str]:
        """Return `result` as a clean string, or None if it isn't a real
        translation of `original`: None / "None" / empty, a provider error
        message, or (for non-ASCII input) the untouched original text that
        some engines hand back when they silently fail."""
        if result is None:
            return None
        text = str(result).strip()
        if not text:
            return None
        if is_none_like(text) and not is_none_like(original):
            return None
        if is_provider_request_error(text) or _is_error_response_impl([text]):
            return None
        if text.casefold() == original.casefold() and not original.isascii():
            return None
        return text

    @staticmethod
    async def atranslate_title(
            text: str,
            target: str = "english",
            source: str = "auto",
            max_fallback_engines: int = 4,
    ) -> t.Optional[str]:
        """Translate a (crawled) title, or return None if no engine could.

        Never returns "None", an empty string or an error message -- that
        is the bug where crawled titles came out as "None". If the Auto
        translation gives nothing usable, every other available engine is
        tried one by one (each with a short retry schedule, at most
        `max_fallback_engines` of them) before giving up. Callers should
        keep the original title when this returns None.
        """
        original = str(text or "").strip()
        if not original:
            return None
        target_code = Translator._normalize_language(target, fallback="en")
        source_code = Translator._normalize_language(source, fallback="auto")

        manager = TranslationManager(engine="auto")
        try:
            usable = Translator._usable_title(
                original, await manager.translate(original, source_code, target_code)
            )
            if usable:
                return usable
        except TranslationError:
            pass

        # Auto's own race already tried (and failed) everything it
        # considered; now go through the engines it skipped or that merely
        # returned something unusable, one at a time.
        tried = set(manager.state.failed_engines)
        if manager.state.active_engine:
            tried.add(manager.state.active_engine)  # it answered, but with junk
        order = list(_translation_settings.auto_engine_order)
        for name in translation_registry.all_registered():
            if name not in order:
                order.append(name)
        fallback = [
            name for name in order
            if name not in tried
            and translation_registry.is_provider_available(name)
            and not _translation_health.is_session_disabled(name)
        ][: max(0, max_fallback_engines)]
        for name in fallback:
            try:
                usable = Translator._usable_title(
                    original,
                    await manager.translate_via(name, original, source_code, target_code, delays=[1]),
                )
            except TranslationError:
                continue
            if usable:
                return usable
        return None

    @staticmethod
    def translate_title(text: str, target: str = "english", source: str = "auto") -> t.Optional[str]:
        return Translator._run_async_blocking(Translator.atranslate_title, text, target, source)

    # -- language detection now tries multiple independent detectors
    # (see translation/detection.py) so a single provider's outage/rate
    # limit can no longer make this return "NA" by itself -- retried
    # (with delay) as a whole chain, since a transient network condition
    # can still affect every network-based detector in the same round.
    @staticmethod
    async def adetect_with_retry(
            text: str,
            retry_delays: t.Optional[t.List[int]] = None,
    ) -> str:
        delays = retry_delays or [2, 5]
        sample = str(text or "").strip()
        if not sample:
            return "NA"

        for attempt in range(len(delays) + 1):
            code = await detect_language_code(sample)
            if code != "NA":
                return code
            if attempt < len(delays):
                await asyncio.sleep(delays[attempt])

        return "NA"

    @staticmethod
    def detect_with_retry(
            text: str,
            retry_delays: t.Optional[t.List[int]] = None,
    ) -> str:
        return Translator._run_async_blocking(
            Translator.adetect_with_retry,
            text,
            retry_delays,
        )

    # -- chunk translation with recovery ---------------------------------
    COULDNT_TRANSLATE_MARKER = "\n\n--->couldn't translate this part\n"

    def _clean_parts(self, parts: t.List[str]) -> t.List[str]:
        """Strip error-page fragments from translated parts. Blank parts are
        dropped; a part is never silently emptied (filter_error_text only
        touches real error-page phrases, see translation/errors.py)."""
        cleaned: t.List[str] = []
        for part in parts:
            value = self._filter_error_text(str(part))
            if value.strip():
                cleaned.append(value)
        return cleaned

    @staticmethod
    def _engines_are_down(exc: BaseException) -> bool:
        """True when a failure says "no engine can serve this right now"
        rather than "this particular text was a problem". Splitting the
        text and trying again cannot help in the first case, and used to
        just multiply the (already long) retry waits."""
        cause = exc.__cause__ or exc
        return isinstance(cause, (AllEnginesFailedError, InvalidEngineError))

    def _translate_text_recovering(
            self, text: str, depth: int = 0, prior_exc: t.Optional[BaseException] = None
    ) -> str:
        """Translate one chunk; if it cannot be translated, return the
        ORIGINAL text behind a visible marker -- text must never just
        disappear from the novel. See _recover_text for the strategy."""
        result = self._recover_text(text, depth, prior_exc)
        return result if result is not None else Translator.COULDNT_TRANSLATE_MARKER + text

    def _recover_text(
            self, text: str, depth: int = 0, prior_exc: t.Optional[BaseException] = None
    ) -> t.Optional[str]:
        """Return the translation of `text`, or None if it could not be
        translated at all. If the first attempt fails for a reason that
        splitting could fix (too long, odd characters in part of it), each
        half is retried once; a mixed result keeps what translated and marks
        only the half that did not.

        `prior_exc` is the failure of an attempt the caller has *already*
        made on exactly this text; it is then not re-sent as-is (that would
        repeat the whole retry schedule for nothing) -- we go straight to
        deciding whether splitting is worthwhile.
        """
        exc = prior_exc
        if exc is None:
            try:
                parts = self._clean_parts(self._translate_batch_with_retry([text]))
                if parts:
                    return "".join(parts)
                exc = RuntimeError("translation came back empty")
            except Exception as e:
                exc = e
        min_chars = _translation_settings.min_recoverable_chunk_chars
        if depth >= 1 or len(text) <= min_chars or self._engines_are_down(exc):
            return None
        halves = split_text(text, max(min_chars, (len(text) + 1) // 2))
        if len(halves) < 2:
            return None
        outs = [self._recover_text(h, depth + 1) for h in halves]
        if all(o is None for o in outs):
            return None  # nothing worked: report the chunk as ONE failure
        # Mixed result: keep what translated, mark only the half that didn't.
        return "".join(
            o if o is not None else Translator.COULDNT_TRANSLATE_MARKER + h
            for o, h in zip(outs, halves)
        )

    def translate(self, chapter: t.List[str], num: int) -> t.Tuple[int, t.List[str]]:
        try:
            translated = self._clean_parts(self._translate_batch_with_retry(chapter))
            if translated or not any(str(c).strip() for c in chapter):
                return num, translated
            raise RuntimeError("translation came back empty")
        except Exception as e:
            try:
                if "text must be a valid text" in str(e):
                    # Legacy googletrans complaint about non-string / numeric
                    # items: drop those and retry once.
                    chapter = [c for c in chapter if isinstance(c, str) and not c.isdigit()]
                    return num, self._clean_parts(self._translate_batch_with_retry(chapter))
                # Item by item (a "chunk" here is normally a single string),
                # each with split-and-retry recovery, instead of the old
                # blind 5s sleep followed by re-sending the very same chunk.
                already_tried = e if len(chapter) == 1 else None
                return num, [
                    self._translate_text_recovering(str(item), prior_exc=already_tried)
                    if str(item).strip() else str(item)
                    for item in chapter
                ]
            except Exception:
                return num, [Translator.COULDNT_TRANSLATE_MARKER + str(tr) for tr in chapter]

    def _make_worker_loop(self, loops: t.List[asyncio.AbstractEventLoop]) -> None:
        """ThreadPoolExecutor initializer: give this worker thread its own
        long-lived event loop (see _worker_local above)."""
        loop = asyncio.new_event_loop()
        _worker_local.loop = loop
        loops.append(loop)  # list.append is atomic; closed by translates()

    @staticmethod
    def _close_worker_loops(loops: t.List[asyncio.AbstractEventLoop]) -> None:
        for loop in loops:
            try:
                if loop.is_running() or loop.is_closed():
                    continue
                loop.run_until_complete(loop.shutdown_asyncgens())
                loop.run_until_complete(loop.shutdown_default_executor())
            except Exception:
                pass
            finally:
                try:
                    loop.close()
                except Exception:
                    pass

    def translates(self, chapters: t.List[str], no_tasks: int) -> None:
        workers = self.get_no_of_workers(no_tasks, len(chapters))
        loops: t.List[asyncio.AbstractEventLoop] = []
        try:
            with concurrent.futures.ThreadPoolExecutor(
                    max_workers=workers,
                    initializer=self._make_worker_loop,
                    initargs=(loops,),
            ) as executor:
                futures = [
                    executor.submit(self.translate, [url], num)
                    for num, url in enumerate(chapters)
                ]
                for future in concurrent.futures.as_completed(futures):
                    result = future.result()
                    self.order[result[0]] = result[1]
                    try:
                        if self.bot.translator[self.user] == "break":
                            executor.shutdown(wait=False, cancel_futures=True)
                            raise Exception("Translation stopped")
                    except Exception as e:
                        if self.bot.translator.get(self.user) == "break":
                            raise e
                        else:
                            pass
                    if len(self.order) % 25 == 0:
                        self.bot.translator[self.user] = f"{len(self.order)}/{len(chapters)}"
        finally:
            Translator._close_worker_loops(loops)

    async def start(self, chapters: t.List[str], no_of_tasks: int = 8) -> str:
        # Bot-wide cap on simultaneously-running translation jobs (see
        # translation/jobs.py) -- protects a resource-constrained host
        # from a burst of concurrent /translate requests each spinning
        # up their own worker-thread pool at once. Jobs beyond the cap
        # queue for a slot rather than being rejected.
        async with await job_limiter.acquire():
            await self.bot.loop.run_in_executor(None, self.translates, chapters, no_of_tasks)
        # Join EVERY part of every chunk, in order. This used to keep only
        # `parts[0]` of each chunk: a failed chunk's result is [marker,
        # original text], so the original text was silently dropped, and a
        # chunk whose result list came back empty raised IndexError and
        # killed the whole job at the very end.
        full_story = "".join(
            "".join(p for p in parts if p is not None)
            for _, parts in sorted(self.order.items(), key=lambda item: item[0])
        )
        self._fold_usage_into_job()
        return full_story

    @staticmethod
    def get_no_of_workers(no_tasks, size) -> int:
        # More chunks = more to gain from concurrency, not less. The old
        # version capped the biggest jobs at 3 workers (the slowest
        # setting), which was backwards. Bump these up if you're not
        # seeing rate-limit errors; dial them back down if you are.
        if size <= 700:
            return 6
        elif size <= 1400:
            return 5
        elif size <= 2000:
            return 5
        else:
            return min(max(no_tasks, 4), 6)

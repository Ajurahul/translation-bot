import asyncio
import concurrent.futures
import threading
import time
import typing as t

from core.bot import Raizel
from languages import languages
from translation.detection import detect_language_code
from translation.errors import TranslationError
from translation.errors import filter_error_text as _filter_error_text_impl
from translation.errors import is_error_response as _is_error_response_impl
from translation.jobs import job_limiter
from translation.manager import TranslationManager
from translation.registry import registry as translation_registry


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

    # -- chunk translation with recovery (unchanged behavior) -----------
    def translate(self, chapter: t.List[str], num: int) -> t.Tuple[int, t.List[str]]:
        translated = []

        def clean_parts(parts: t.List[str]) -> t.List[str]:
            cleaned: t.List[str] = []
            for part in parts:
                value = self._filter_error_text(str(part))
                if value.strip():
                    cleaned.append(value)
            return cleaned

        try:
            translated = self._translate_batch_with_retry(chapter)
            translated = clean_parts(translated)
        except Exception as e:
            try:
                if "text must be a valid text" in str(e):
                    for c in chapter[:]:  # Use slice to avoid modifying while iterating
                        if not isinstance(c, str) or c.isdigit():
                            chapter.remove(c)
                    translated = self._translate_batch_with_retry(chapter)
                    translated = clean_parts(translated)
                else:
                    time.sleep(5)
                    chp1 = chapter[:len(chapter) // 2]
                    chp2 = chapter[len(chapter) // 2:]

                    # Try first half
                    try:
                        translated = self._translate_batch_with_retry(chp1)
                        translated = clean_parts(translated)
                    except Exception as e1:
                        translated = chp1
                        translated.insert(0, "\n\n--->couldn't translate this part")

                    # Try second half
                    new_tr = []
                    try:
                        new_tr = self._translate_batch_with_retry(chp2)
                        new_tr = clean_parts(new_tr)
                    except Exception as e2:
                        new_tr = chp2[:]
                        new_tr.insert(0, "\n\n--->couldn't translate this part")

                    for tr in new_tr:
                        translated.append(tr)
            except:
                for tr in chapter:
                    translated.append("\n\n--->couldn't translate this part")
                    translated.append(tr)

        return num, translated

    def translates(self, chapters: t.List[str], no_tasks: int) -> None:
        workers = self.get_no_of_workers(no_tasks, len(chapters))
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
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
                    if self.bot.translator[self.user] == "break":
                        raise e
                    else:
                        pass
                if len(self.order) % 25 == 0:
                    self.bot.translator[self.user] = f"{len(self.order)}/{len(chapters)}"

    async def start(self, chapters: t.List[str], no_of_tasks: int = 8) -> str:
        # Bot-wide cap on simultaneously-running translation jobs (see
        # translation/jobs.py) -- protects a resource-constrained host
        # from a burst of concurrent /translate requests each spinning
        # up their own worker-thread pool at once. Jobs beyond the cap
        # queue for a slot rather than being rejected.
        async with await job_limiter.acquire():
            await self.bot.loop.run_in_executor(None, self.translates, chapters, no_of_tasks)
        ordered_story = {
            k: v for k, v in sorted(self.order.items(), key=lambda item: item[0])
        }
        full_story = [i[0] for i in list(ordered_story.values()) if i[0] is not None]
        self._fold_usage_into_job()
        return "".join(full_story)

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

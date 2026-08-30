import asyncio
import os
import threading
try:
    from asyncio import Timeout
except ImportError:
    from async_timeout import Timeout


import concurrent.futures
import re
import time
import typing as t

from deep_translator import GoogleTranslator as DeepGoogleTranslator
from googletrans import Translator as GoogleTransClient

# The `translators` package (used for the Bing engine, and by the new
# translator/backends/translators_pkg_engine.py for a few additional free
# providers) tries to auto-detect the server's region over the network on
# first import. If that lookup fails -- no route out, restrictive
# firewall/proxy, offline dev box -- it falls back to an interactive
# `input()` prompt, which hangs forever in any headless environment (a
# Docker container, a CI runner, systemd with no TTY, ...). Setting this
# env var before the import skips that detection entirely; it only picks
# which server pool `translators` prefers, it does not restrict which
# providers are reachable. Must be set before the import below.
os.environ.setdefault("translators_default_region", "EN")

try:
    import translators as bing_translators
    _BING_AVAILABLE = True
except ImportError:
    bing_translators = None
    _BING_AVAILABLE = False

from core.bot import Raizel
from languages import languages


class Translator:
    GOOGLETRANS_SERVICE_URLS = [
        "translate.google.com",
        "translate.google.co.in",
        "translate.google.co.kr",
        "translate.google.co.uk",
        "translate.google.ca",
        "translate.google.com.au",
        "translate.google.de",
        "translate.google.fr",
        "translate.google.es",
        "translate.google.it",
        "translate.google.co.jp",
    ]

    # Fixed try-order per round: deep translator first, then googletrans,
    # then bing (if the optional dependency is installed). Every round
    # tries each of these exactly once before giving up on that round.
    ENGINE_SEQUENCE = ["deep", "googletrans"] + (["bing"] if _BING_AVAILABLE else [])

    # deep_translator/googletrans use Google's own language codes (from
    # languages.py). Bing/Microsoft expects different codes for a few of
    # these -- notably Chinese. Only include entries where they actually
    # differ; anything not listed here is passed through unchanged (this
    # covers ko/en/id and the vast majority of other languages already).
    BING_LANGUAGE_OVERRIDES: t.Dict[str, str] = {
        "zh-cn": "zh-Hans",
        "zh-tw": "zh-Hant",
        "iw": "he",     # Google's old code for Hebrew
        "tl": "fil",    # Google uses "tl" (Tagalog) for what Bing calls Filipino
    }

    # --- Circuit breaker -------------------------------------------------
    # If an engine keeps failing across *any* worker (not just within a
    # single call's own retry loop), put it in a short cooldown so every
    # other concurrent chunk stops wasting attempts on it too. This matters
    # a lot once you have several chunks translating in parallel: without
    # this, each one independently retries the dead engine before falling
    # back, all at the same time.
    #
    # NOTE: cooldown only *deprioritizes* an engine within a round -- if
    # every engine happens to be cooling down at once we still try all of
    # them anyway (see _get_engine_order), so a round is never skipped
    # entirely.
    ENGINE_COOLDOWN_THRESHOLD = 2        # consecutive global failures before cooldown
    ENGINE_COOLDOWN_SECONDS = 90         # how long to avoid a tripped engine
    # Hard ceiling on a single engine call. Without this, a stalled/blocked
    # engine (no response, connection hangs) can block a worker thread far
    # longer than any of the retry_delays below would suggest -- this is
    # what turns a handful of bad chunks into a 45-minute job.
    ENGINE_CALL_TIMEOUT_SECONDS = 10
    _engine_failure_counts: t.Dict[str, int] = {}
    _engine_cooldown_until: t.Dict[str, float] = {}
    _engine_lock = threading.Lock()

    def __init__(self, bot: Raizel, user: int, language: str,
                 engine_mode: t.Optional[str] = None,
                 on_engine_switch: t.Optional[t.Callable[[str, str], None]] = None,
                 translation_manager: t.Optional["object"] = None) -> None:
        self.bot = bot
        self.user = user
        self.language = language
        self.order = {}
        # `engine_mode` selects which of the new runtime-selectable
        # translation engines to use ("auto", "default", or a concrete
        # engine key like "googletrans"/"bing"/...). Leaving it as None
        # (the default) preserves the exact legacy behaviour below --
        # the fixed deep -> googletrans -> bing cascade with the
        # class-level circuit breaker -- for every existing caller that
        # doesn't know about engine selection (e.g. the bare
        # `Translator.atranslate_with_retry(...)` calls used for
        # title/description translation elsewhere in the bot).
        self.engine_mode = engine_mode
        self._on_engine_switch = on_engine_switch
        # BUG FIX (found in review): large-file translation in
        # cogs/translation.py deliberately builds a *new* `Translator`
        # instance per 1000-line chunk-batch and `del`s the old one, to
        # keep memory bounded across a big novel. If each new instance
        # also lazily built its own fresh TranslationManager, Auto mode's
        # "remember which engine works" state (and Explicit mode's
        # unavailable-engine check) would silently reset every 1000
        # lines instead of persisting for the whole /translate job --
        # exactly the "re-probe every chunk" anti-pattern the spec
        # forbids, just at chunk-batch granularity instead of per-chunk.
        # Callers that span multiple Translator instances for one logical
        # job (see cogs/translation.py) now build ONE TranslationManager
        # up front and pass it in here explicitly; callers that only ever
        # create a single Translator per job (the common case, and every
        # existing caller) can keep leaving this as None and get a
        # private manager built lazily, unchanged from before.
        self._manager = translation_manager

    def _get_manager(self):
        if self._manager is None:
            from translator.manager import TranslationManager
            self._manager = TranslationManager(
                engine_mode=self.engine_mode,
                on_engine_switch=self._on_engine_switch,
            )
        return self._manager

    @classmethod
    def _is_engine_cooling_down(cls, engine: str) -> bool:
        until = cls._engine_cooldown_until.get(engine)
        return until is not None and time.time() < until

    @classmethod
    def _note_engine_failure(cls, engine: str) -> None:
        with cls._engine_lock:
            count = cls._engine_failure_counts.get(engine, 0) + 1
            if count >= cls.ENGINE_COOLDOWN_THRESHOLD:
                cls._engine_cooldown_until[engine] = time.time() + cls.ENGINE_COOLDOWN_SECONDS
                count = 0
            cls._engine_failure_counts[engine] = count

    @classmethod
    def _note_engine_success(cls, engine: str) -> None:
        with cls._engine_lock:
            cls._engine_failure_counts[engine] = 0
            cls._engine_cooldown_until.pop(engine, None)

    @classmethod
    def _get_engine_order(cls) -> t.List[str]:
        """Fixed deep -> googletrans -> bing order. `deep` is the cheap,
        reliable, low-latency engine and should always be tried first
        while it's healthy -- googletrans/bing are pure fallbacks for
        when deep is down, not alternatives to promote just because they
        happened to succeed once (they're both noticeably slower per call
        even when they work, so promoting them tanks average throughput).
        Anything currently cooling down (repeated recent failures) is
        skipped entirely, unless every engine is cooling down, in which
        case we fall back to trying all of them rather than nothing."""
        healthy = [e for e in cls.ENGINE_SEQUENCE if not cls._is_engine_cooling_down(e)]
        return healthy if healthy else list(cls.ENGINE_SEQUENCE)

    @staticmethod
    def _is_error_500_response(translated: t.List[str]) -> bool:
        """Detect if response is a Google Translate error page.

        Delegates to `translator.errors.is_error_response`, the single
        canonical implementation shared with the new engine backends, so
        the detector only lives in one place (see translator/errors.py)."""
        from translator.errors import is_error_response
        return is_error_response(translated)

    @staticmethod
    def _filter_error_text(text: str) -> str:
        """Remove error messages from text as a safety measure.

        Delegates to `translator.errors.filter_error_text` -- see
        `_is_error_500_response` above."""
        from translator.errors import filter_error_text
        return filter_error_text(text)

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

    @staticmethod
    def _validate_and_clean_translations(translated: t.List[str]) -> t.List[str]:
        if Translator._is_error_500_response(translated):
            raise RuntimeError("translation returned Error 500 response body")

        cleaned = [Translator._filter_error_text(str(text)) for text in translated]

        # Re-check after filtering so partial error payloads are never returned.
        if Translator._is_error_500_response(cleaned):
            raise RuntimeError("translation returned Error 500 response body")

        return cleaned

    @staticmethod
    async def _translate_text_with_deep(
            text: str,
            target_code: str,
            source_code: str,
    ) -> str:
        def _work() -> str:
            translator = DeepGoogleTranslator(source=source_code, target=target_code)
            return str(translator.translate(str(text)))

        return await asyncio.to_thread(_work)

    @staticmethod
    async def _translate_batch_with_deep(
            chapter: t.List[str],
            target_code: str,
            source_code: str,
    ) -> t.List[str]:
        def _work() -> t.List[str]:
            translator = DeepGoogleTranslator(source=source_code, target=target_code)
            if hasattr(translator, "translate_batch"):
                translated = translator.translate_batch(chapter)
                return [str(item) for item in translated]
            return [str(translator.translate(str(item))) for item in chapter]

        return await asyncio.to_thread(_work)

    @staticmethod
    async def _translate_text_with_googletrans(
            text: str,
            target_code: str,
            source_code: str,
    ) -> str:
        async with GoogleTransClient(
                timeout=Timeout(15.0),
                raise_exception=True,
                service_urls=Translator.GOOGLETRANS_SERVICE_URLS,
        ) as translator:
            translated = await translator.translate(
                str(text),
                dest=target_code,
                src=source_code,
            )
        return str(getattr(translated, "text", translated))

    @staticmethod
    async def _translate_batch_with_googletrans(
            chapter: t.List[str],
            target_code: str,
            source_code: str,
    ) -> t.List[str]:
        async with GoogleTransClient(
                timeout=Timeout(15.0),
                raise_exception=True,
                service_urls=Translator.GOOGLETRANS_SERVICE_URLS,
        ) as translator:
            translated = await translator.translate(
                chapter,
                dest=target_code,
                src=source_code,
            )
        if not isinstance(translated, list):
            translated = [translated]
        return [str(getattr(item, "text", item)) for item in translated]

    @staticmethod
    def _to_bing_code(code: str) -> str:
        if not code or code == "auto":
            return "auto"
        return Translator.BING_LANGUAGE_OVERRIDES.get(code.lower(), code)

    @staticmethod
    async def _translate_text_with_bing(
            text: str,
            target_code: str,
            source_code: str,
    ) -> str:
        def _work() -> str:
            result = bing_translators.translate_text(
                str(text),
                translator="bing",
                from_language=Translator._to_bing_code(source_code),
                to_language=Translator._to_bing_code(target_code),
            )
            return str(result)

        return await asyncio.to_thread(_work)

    @staticmethod
    async def _translate_batch_with_bing(
            chapter: t.List[str],
            target_code: str,
            source_code: str,
    ) -> t.List[str]:
        def _work() -> t.List[str]:
            from_code = Translator._to_bing_code(source_code)
            to_code = Translator._to_bing_code(target_code)
            return [
                str(bing_translators.translate_text(
                    str(item),
                    translator="bing",
                    from_language=from_code,
                    to_language=to_code,
                ))
                for item in chapter
            ]

        return await asyncio.to_thread(_work)

    @staticmethod
    async def _translate_text_with_engine(
            engine: str,
            text: str,
            target_code: str,
            source_code: str,
    ) -> str:
        if engine == "deep":
            coro = Translator._translate_text_with_deep(
                text=text, target_code=target_code, source_code=source_code,
            )
        elif engine == "bing":
            coro = Translator._translate_text_with_bing(
                text=text, target_code=target_code, source_code=source_code,
            )
        else:
            coro = Translator._translate_text_with_googletrans(
                text=text, target_code=target_code, source_code=source_code,
            )
        # Hard ceiling so a stalled engine can never block a worker
        # indefinitely -- deep_translator and translators (bing) don't
        # enforce their own timeout, so without this a single hung call
        # can eat minutes instead of failing over to the next engine.
        return await asyncio.wait_for(coro, timeout=Translator.ENGINE_CALL_TIMEOUT_SECONDS)

    @staticmethod
    async def _translate_batch_with_engine(
            engine: str,
            chapter: t.List[str],
            target_code: str,
            source_code: str,
    ) -> t.List[str]:
        if engine == "deep":
            coro = Translator._translate_batch_with_deep(
                chapter=chapter, target_code=target_code, source_code=source_code,
            )
        elif engine == "bing":
            coro = Translator._translate_batch_with_bing(
                chapter=chapter, target_code=target_code, source_code=source_code,
            )
        else:
            coro = Translator._translate_batch_with_googletrans(
                chapter=chapter, target_code=target_code, source_code=source_code,
            )
        return await asyncio.wait_for(coro, timeout=Translator.ENGINE_CALL_TIMEOUT_SECONDS)

    @staticmethod
    async def adetect_with_retry(
            text: str,
            retry_delays: t.Optional[t.List[int]] = None,
    ) -> str:
        delays = retry_delays or [2, 5]
        last_error: t.Optional[Exception] = None
        sample = str(text or "").strip()
        if not sample:
            return "NA"

        for attempt in range(len(delays) + 1):
            try:
                async def _detect():
                    async with GoogleTransClient(
                            timeout=Timeout(10.0),
                            raise_exception=True,
                            service_urls=Translator.GOOGLETRANS_SERVICE_URLS,
                    ) as translator:
                        return await translator.detect(sample)

                result = await asyncio.wait_for(_detect(), timeout=10.0)
                lang = str(getattr(result, "lang", "NA") or "NA").lower()
                return lang
            except Exception as e:
                last_error = e
                if attempt < len(delays):
                    await asyncio.sleep(delays[attempt])

        raise last_error or RuntimeError("language detection failed after retries")

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

    @staticmethod
    async def atranslate_with_retry(
            text: str,
            target: str = "english",
            source: str = "auto",
            retry_delays: t.Optional[t.List[int]] = None,
    ) -> str:
        """Each round tries whichever engines are currently healthy, in
        order. If a whole round fails (every healthy engine errored), wait
        the next delay in `retry_delays` and run another round. Kept short
        on purpose -- the caller (`translate()`) already retries again by
        splitting the chapter in half, so we don't want two multi-round
        retry loops stacked on top of each other."""
        delays = retry_delays or [2, 5]
        target_code = Translator._normalize_language(target, fallback="en")
        source_code = Translator._normalize_language(source, fallback="auto")

        last_error: t.Optional[Exception] = None

        for round_index in range(len(delays) + 1):
            if round_index > 0:
                await asyncio.sleep(delays[round_index - 1])

            for engine in Translator._get_engine_order():
                try:
                    translated_text = await Translator._translate_text_with_engine(
                        engine=engine,
                        text=str(text),
                        target_code=target_code,
                        source_code=source_code,
                    )

                    cleaned = Translator._validate_and_clean_translations([translated_text])
                    Translator._note_engine_success(engine)
                    return cleaned[0]
                except Exception as e:
                    last_error = e
                    Translator._note_engine_failure(engine)

        raise last_error or RuntimeError("translation failed after retries")

    @staticmethod
    def translate_with_retry(
            text: str,
            target: str = "english",
            source: str = "auto",
            retry_delays: t.Optional[t.List[int]] = None,
    ) -> str:
        return Translator._run_async_blocking(
            Translator.atranslate_with_retry,
            text,
            target,
            source,
            retry_delays,
        )

    @staticmethod
    async def atranslate_batch_with_retry(
            chapter: t.List[str],
            target: str,
            source: str = "auto",
            retry_delays: t.Optional[t.List[int]] = None,
    ) -> t.List[str]:
        """Same round-robin/backoff scheme as atranslate_with_retry, but
        for a batch of strings translated together."""
        delays = retry_delays or [2, 5]
        target_code = Translator._normalize_language(target, fallback="en")
        source_code = Translator._normalize_language(source, fallback="auto")

        last_error: t.Optional[Exception] = None

        for round_index in range(len(delays) + 1):
            if round_index > 0:
                await asyncio.sleep(delays[round_index - 1])

            for engine in Translator._get_engine_order():
                try:
                    translated_texts = await Translator._translate_batch_with_engine(
                        engine=engine,
                        chapter=chapter,
                        target_code=target_code,
                        source_code=source_code,
                    )

                    cleaned = Translator._validate_and_clean_translations(translated_texts)
                    Translator._note_engine_success(engine)
                    return cleaned
                except Exception as e:
                    last_error = e
                    Translator._note_engine_failure(engine)

        raise last_error or RuntimeError("translation failed after retries")

    @staticmethod
    def translate_batch_with_retry(
            chapter: t.List[str],
            target: str,
            source: str = "auto",
            retry_delays: t.Optional[t.List[int]] = None,
    ) -> t.List[str]:
        return Translator._run_async_blocking(
            Translator.atranslate_batch_with_retry,
            chapter,
            target,
            source,
            retry_delays,
        )

    def _translate_batch_with_retry(self, chapter: t.List[str]) -> t.List[str]:
        if self.engine_mode:
            # New runtime-selectable-engine path (explicit engine, Default,
            # or Auto). `translate()`/`translates()` below call this from
            # ThreadPoolExecutor worker threads with no running event loop,
            # exactly like the legacy path via `_run_async_blocking`.
            manager = self._get_manager()
            return self._run_async_blocking(
                manager.translate_many, chapter, "auto", self.language,
            )
        return self.translate_batch_with_retry(
            chapter=chapter,
            source="auto",
            target=self.language,
        )

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
            if self.engine_mode:
                # New-style engine selection (explicit engine, Default, or
                # Auto): a `TranslationFailedError` here already means the
                # manager exhausted retries (and, for a single overly-long
                # chunk, its own char-level split/recovery -- section 17).
                # Splitting the *chapter list* and retrying the same
                # already-exhausted engine/pool would not help, and
                # per Rule 1 we must not silently fall back to a
                # different engine for an explicit selection -- so this
                # propagates up as a hard failure instead of being
                # swallowed into "couldn't translate this part"
                # placeholder text the way the legacy cascade does below.
                from translator.base import TranslationFailedError
                if isinstance(e, TranslationFailedError):
                    raise
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
        await self.bot.loop.run_in_executor(None, self.translates, chapters, no_of_tasks)
        ordered_story = {
            k: v for k, v in sorted(self.order.items(), key=lambda item: item[0])
        }
        full_story = [i[0] for i in list(ordered_story.values()) if i[0] is not None]
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
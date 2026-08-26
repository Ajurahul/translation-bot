import asyncio
from asyncio import Timeout

import concurrent.futures
import re
import time
import typing as t

from deep_translator import GoogleTranslator as DeepGoogleTranslator
from googletrans import Translator as GoogleTransClient

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

    DEEP_TRANSLATOR_FALLBACK_AFTER = 2

    def __init__(self, bot: Raizel, user: int, language: str) -> None:
        self.bot = bot
        self.user = user
        self.language = language
        self.order = {}

    @staticmethod
    def _is_error_500_response(translated: t.List[str]) -> bool:
        """Detect if response is a Google Translate error page."""
        if not translated:
            return False

        joined = " ".join(str(part) for part in translated).lower()
        joined = joined.replace("’", "'")

        # Error page markers
        error_indicators = (
            "error 500",
            "server error",
            "that's an error",
            "there was an error",
            "please try again later",
            "that's all we know",
        )

        # Count how many error indicators are present
        marker_hits = sum(indicator in joined for indicator in error_indicators)

        # If we see "error 500" OR multiple error markers, it's an error response
        is_error = ("error 500" in joined) or (marker_hits >= 2)

        return is_error

    @staticmethod
    def _filter_error_text(text: str) -> str:
        """Remove error messages from text as a safety measure."""
        if not text:
            return text

        # Pattern to detect and remove error pages
        error_patterns = [
            r"Error 500.*?(?=\n[A-Za-zÀ-ÿ]|\Z)",  # Error 500 followed by actual text or end
            r"(?:error|server error|that's an error|there was an error|please try again later).*?(?=\n[A-Za-zÀ-ÿ]|\Z)",
        ]

        result = text
        for pattern in error_patterns:
            result = re.sub(pattern, "", result, flags=re.IGNORECASE | re.DOTALL)

        return result.strip()

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
    async def adetect_with_retry(
            text: str,
            retry_delays: t.Optional[t.List[int]] = None,
    ) -> str:
        delays = retry_delays or [2, 4, 7, 10]
        last_error: t.Optional[Exception] = None
        sample = str(text or "").strip()
        if not sample:
            return "NA"

        for attempt in range(len(delays) + 1):
            try:
                async with GoogleTransClient(
                        timeout=Timeout(15.0),
                        raise_exception=True,
                        service_urls=Translator.GOOGLETRANS_SERVICE_URLS,
                ) as translator:
                    result = await translator.detect(sample)
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
        delays = retry_delays or [2, 4, 7, 10]
        last_error: t.Optional[Exception] = None
        target_code = Translator._normalize_language(target, fallback="en")
        source_code = Translator._normalize_language(source, fallback="auto")
        deep_failures = 0

        for attempt in range(len(delays) + 1):
            use_deep = deep_failures < Translator.DEEP_TRANSLATOR_FALLBACK_AFTER
            try:
                if use_deep:
                    translated_text = await Translator._translate_text_with_deep(
                        text=str(text),
                        target_code=target_code,
                        source_code=source_code,
                    )
                else:
                    translated_text = await Translator._translate_text_with_googletrans(
                        text=str(text),
                        target_code=target_code,
                        source_code=source_code,
                    )

                cleaned = Translator._validate_and_clean_translations([translated_text])
                return cleaned[0]
            except Exception as e:
                last_error = e
                if use_deep:
                    deep_failures += 1
                if attempt < len(delays):
                    await asyncio.sleep(delays[attempt])

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
        delays = retry_delays or [2, 4, 7, 10]
        last_error: t.Optional[Exception] = None
        target_code = Translator._normalize_language(target, fallback="en")
        source_code = Translator._normalize_language(source, fallback="auto")
        deep_failures = 0

        for attempt in range(len(delays) + 1):
            use_deep = deep_failures < Translator.DEEP_TRANSLATOR_FALLBACK_AFTER
            try:
                if use_deep:
                    translated_texts = await Translator._translate_batch_with_deep(
                        chapter=chapter,
                        target_code=target_code,
                        source_code=source_code,
                    )
                else:
                    translated_texts = await Translator._translate_batch_with_googletrans(
                        chapter=chapter,
                        target_code=target_code,
                        source_code=source_code,
                    )

                return Translator._validate_and_clean_translations(translated_texts)
            except Exception as e:
                last_error = e
                if use_deep:
                    deep_failures += 1
                if attempt < len(delays):
                    await asyncio.sleep(delays[attempt])

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
        if size <= 700:
            return 5
        elif size <= 1400:
            return 4
        elif size <= 2000:
            return 3
        else:
            return min(no_tasks, 3) if no_tasks > 8 else 5

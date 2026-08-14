import concurrent.futures
import time
import typing as t

from deep_translator import GoogleTranslator

from core.bot import Raizel


class Translator:
    def __init__(self, bot: Raizel, user: int, language: str) -> None:
        self.bot = bot
        self.user = user
        self.language = language
        self.order = {}

    @staticmethod
    def _is_error_500_response(translated: t.List[str]) -> bool:
        if not translated:
            return False

        joined = " ".join(str(part) for part in translated).lower()
        joined = joined.replace("’", "'")

        markers = (
            "error 500",
            "server error",
            "that's an error",
            "there was an error",
            "please try again later",
        )

        marker_hits = sum(marker in joined for marker in markers)
        return "error 500" in joined and marker_hits >= 2

    @staticmethod
    def translate_with_retry(
            text: str,
            target: str = "english",
            source: str = "auto",
            retry_delays: t.Optional[t.List[int]] = None,
    ) -> str:
        delays = retry_delays or [2, 4, 7, 10]
        last_error: t.Optional[Exception] = None

        for attempt in range(len(delays) + 1):
            try:
                translated = GoogleTranslator(source=source, target=target).translate(text)
                if Translator._is_error_500_response([str(translated)]):
                    raise RuntimeError("translation returned Error 500 response body")
                return translated
            except Exception as e:
                last_error = e
                if attempt < len(delays):
                    time.sleep(delays[attempt])

        raise last_error or RuntimeError("translation failed after retries")

    @staticmethod
    def translate_batch_with_retry(
            chapter: t.List[str],
            target: str,
            source: str = "auto",
            retry_delays: t.Optional[t.List[int]] = None,
    ) -> t.List[str]:
        delays = retry_delays or [2, 4, 7, 10]
        last_error: t.Optional[Exception] = None

        for attempt in range(len(delays) + 1):
            try:
                translated = GoogleTranslator(source=source, target=target).translate_batch(chapter)
                if Translator._is_error_500_response(translated):
                    raise RuntimeError("translation returned Error 500 response body")
                return translated
            except Exception as e:
                last_error = e
                if attempt < len(delays):
                    time.sleep(delays[attempt])

        raise last_error or RuntimeError("translation failed after retries")

    def _translate_batch_with_retry(self, chapter: t.List[str]) -> t.List[str]:
        return self.translate_batch_with_retry(
            chapter=chapter,
            source="auto",
            target=self.language,
        )

    def translate(self, chapter: t.List[str], num: int) -> t.Tuple[int, t.List[str]]:
        translated = []
        try:
            translated = self._translate_batch_with_retry(chapter)
        except Exception as e:
            try:
                if "text must be a valid text" in str(e):
                    for c in chapter:
                        if not isinstance(c, str) or c.isdigit():
                            chapter.remove(c)
                    translated = self._translate_batch_with_retry(chapter)
                else:
                    time.sleep(5)
                    while True:
                        chp1 = chapter[:len(chapter) // 2]
                        chp2 = chapter[len(chapter) // 2:]
                        try:
                            translated = self._translate_batch_with_retry(chp1)
                        except:
                            translated = chp1
                            translated.insert(0, "\n\n--->couldn't translate this part")
                            chapter = chp1
                        new_tr = []
                        try:
                            new_tr = self._translate_batch_with_retry(chp2)
                        except:
                            chp2.insert(0, "\n\n--->couldn't translate this part")
                            chapter = chp2
                        for tr in new_tr:
                            translated.append(tr)
                        break
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
            return 10
        elif size <= 1400:
            return 9
        elif size <= 2000:
            return 8
        else:
            return min(no_tasks, 7) if no_tasks > 8 else 8

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

    def translate(self, chapter: t.List[str], num: int) -> t.Tuple[int, t.List[str]]:
        translated = []
        try:
            try:
                translated = GoogleTranslator(
                    source="auto", target=self.language
                ).translate_batch(chapter)
            except:
                time.sleep(3)
                translated = GoogleTranslator(
                    source="auto", target=self.language
                ).translate_batch(chapter)
        except Exception as e:
            try:
                if "text must be a valid text" in str(e):
                    for c in chapter:
                        if not isinstance(c, str) or c.isdigit():
                            chapter.remove(c)
                    translated = GoogleTranslator(source="auto", target=self.language).translate_batch(chapter)
                else:
                    time.sleep(5)
                    while True:
                        chp1 = chapter[:len(chapter) // 2]
                        chp2 = chapter[len(chapter) // 2:]
                        try:
                            translated = GoogleTranslator(source="auto", target=self.language).translate_batch(chp1)
                        except:
                            translated = chp1
                            translated.insert(0, "\n\n--->couldn't translate this part")
                            chapter = chp1
                        try:
                            new_tr = GoogleTranslator(source="auto", target=self.language).translate_batch(chp2)
                        except:
                            chp2.insert(0, "\n\n--->couldn't translate this part")
                            chapter = chp2
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
            return 10
        elif size <= 1400:
            return 9
        elif size <= 2000:
            return 8
        else:
            return min(no_tasks, 7) if no_tasks > 8 else 8

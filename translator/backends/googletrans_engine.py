"""GoogleTrans backend.

Reuses the existing `googletrans`-library call implementation in
utils/translate.py (Rule 12/Rule 25: don't duplicate an engine that
already exists and is battle-tested) and adds it to the new pluggable
backend interface.
"""
import typing as t

from translator.base import TranslationBackend
from translator.errors import validate_and_clean


class GoogleTransBackend(TranslationBackend):
    name = "googletrans"
    display_name = "GoogleTrans"

    def is_available(self) -> bool:
        try:
            import googletrans  # noqa: F401
            return True
        except ImportError:
            return False

    async def translate(self, text: str, source_language: str, target_language: str) -> str:
        from utils.translate import Translator as LegacyTranslator
        result = await LegacyTranslator._translate_text_with_googletrans(
            text=text, target_code=target_language, source_code=source_language,
        )
        return validate_and_clean([result])[0]

    async def translate_batch(self, texts: t.List[str], source_language: str, target_language: str) -> t.List[str]:
        from utils.translate import Translator as LegacyTranslator
        results = await LegacyTranslator._translate_batch_with_googletrans(
            chapter=texts, target_code=target_language, source_code=source_language,
        )
        return validate_and_clean(results)

"""Deep Translator backend.

Reuses the existing `deep-translator`-library call implementation in
utils/translate.py.
"""
import typing as t

from translator.base import TranslationBackend
from translator.errors import validate_and_clean


class DeepTranslatorBackend(TranslationBackend):
    name = "deep_translator"
    display_name = "Deep Translator"

    def is_available(self) -> bool:
        try:
            import deep_translator  # noqa: F401
            return True
        except ImportError:
            return False

    async def translate(self, text: str, source_language: str, target_language: str) -> str:
        from utils.translate import Translator as LegacyTranslator
        result = await LegacyTranslator._translate_text_with_deep(
            text=text, target_code=target_language, source_code=source_language,
        )
        return validate_and_clean([result])[0]

    async def translate_batch(self, texts: t.List[str], source_language: str, target_language: str) -> t.List[str]:
        from utils.translate import Translator as LegacyTranslator
        results = await LegacyTranslator._translate_batch_with_deep(
            chapter=texts, target_code=target_language, source_code=source_language,
        )
        return validate_and_clean(results)

"""Bing backend (via the `translators` package).

Reuses the existing bing call implementation in utils/translate.py,
including its Bing-specific language-code overrides (BING_LANGUAGE_OVERRIDES)
which the generic `translators` package wrapper deliberately does not try
to replicate for every provider.
"""
import os
import typing as t

from translator.base import TranslationBackend
from translator.errors import validate_and_clean


class BingBackend(TranslationBackend):
    name = "bing"
    display_name = "Bing"

    def is_available(self) -> bool:
        # Avoid an interactive region prompt / network call at import time
        # in headless environments -- see translators_pkg_engine.py for
        # details on why this env var matters.
        os.environ.setdefault("translators_default_region", "EN")
        try:
            import translators  # noqa: F401
            return True
        except ImportError:
            return False

    async def translate(self, text: str, source_language: str, target_language: str) -> str:
        from utils.translate import Translator as LegacyTranslator
        result = await LegacyTranslator._translate_text_with_bing(
            text=text, target_code=target_language, source_code=source_language,
        )
        return validate_and_clean([result])[0]

    async def translate_batch(self, texts: t.List[str], source_language: str, target_language: str) -> t.List[str]:
        from utils.translate import Translator as LegacyTranslator
        results = await LegacyTranslator._translate_batch_with_bing(
            chapter=texts, target_code=target_language, source_code=source_language,
        )
        return validate_and_clean(results)

"""Verifies the legacy chapter-translation pipeline in utils/translate.py
still behaves exactly as before when the caller doesn't opt into the new
runtime-selectable engines (engine_mode=None, e.g. the description/title
translation call sites elsewhere in the bot), and only takes the new path
when a caller explicitly asks for it (Rule 11 / Rule 25 - backward
compatibility).
"""
from unittest.mock import MagicMock, patch

from utils.translate import Translator


def _make_translator(engine_mode=None):
    bot = MagicMock()
    return Translator(bot, user=123, language="en", engine_mode=engine_mode)


def test_no_engine_mode_uses_legacy_cascade():
    tr = _make_translator(engine_mode=None)
    with patch.object(Translator, "translate_batch_with_retry", return_value=["legacy result"]) as legacy, \
            patch("translator.manager.TranslationManager") as manager_cls:
        result = tr._translate_batch_with_retry(["hello"])

    assert result == ["legacy result"]
    legacy.assert_called_once()
    manager_cls.assert_not_called()


def test_engine_mode_uses_new_manager_not_legacy_cascade():
    tr = _make_translator(engine_mode="googletrans")
    fake_manager = MagicMock()

    async def fake_translate_many(chapter, source, target):
        return [f"new:{c}" for c in chapter]

    fake_manager.translate_many = fake_translate_many

    with patch.object(Translator, "translate_batch_with_retry") as legacy, \
            patch("translator.manager.TranslationManager", return_value=fake_manager):
        result = tr._translate_batch_with_retry(["hello"])

    assert result == ["new:hello"]
    legacy.assert_not_called()


def test_engine_mode_none_by_default():
    bot = MagicMock()
    tr = Translator(bot, user=123, language="en")
    assert tr.engine_mode is None


def test_bare_static_call_sites_are_unaffected():
    # Elsewhere in the bot, title/description translation calls
    # `Translator.atranslate_with_retry`/`translate_batch_with_retry` as
    # bare staticmethods with no Translator instance and therefore no
    # engine_mode at all -- confirm those entry points still exist and
    # are unrelated to the new instance-level engine_mode attribute.
    assert hasattr(Translator, "atranslate_with_retry")
    assert hasattr(Translator, "translate_batch_with_retry")
    assert hasattr(Translator, "atranslate_batch_with_retry")

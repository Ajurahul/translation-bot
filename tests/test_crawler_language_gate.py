import sys
import types

if "mega" not in sys.modules:
    _mega_stub = types.ModuleType("mega")
    _mega_stub.Mega = type("Mega", (), {})
    sys.modules["mega"] = _mega_stub

from utils.handler import FileHandler


def test_skips_when_language_is_na():
    assert FileHandler.should_auto_translate("NA", None, None) is False
    assert FileHandler.should_auto_translate("na", None, None) is False
    assert FileHandler.should_auto_translate(None, None, None) is False


def test_skips_when_already_english():
    assert FileHandler.should_auto_translate("english", None, None) is False
    assert FileHandler.should_auto_translate("English", None, None) is False
    assert FileHandler.should_auto_translate("en", None, None) is False


def test_triggers_for_a_real_detected_non_english_language():
    assert FileHandler.should_auto_translate("chinese (simplified)", None, None) is True
    assert FileHandler.should_auto_translate("french", None, None) is True


def test_skips_when_caller_already_chose_a_target_or_terms():
    assert FileHandler.should_auto_translate("chinese (simplified)", "spanish", None) is False
    assert FileHandler.should_auto_translate("chinese (simplified)", None, "naruto") is False

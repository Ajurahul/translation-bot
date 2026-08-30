import json

from translation.config import DEFAULT_CONFIG, TranslationSettings
from translation.manager import TranslationManager
from translation.registry import ProviderRegistry


def test_default_engine_persists_across_reload(tmp_path):
    path = tmp_path / "translation_settings.json"
    settings = TranslationSettings(path=path)
    assert settings.default_engine == DEFAULT_CONFIG["default_engine"]

    settings.set_default_engine("translators-bing")
    assert path.exists()

    reloaded = TranslationSettings(path=path)
    assert reloaded.default_engine == "translators-bing"


def test_missing_settings_file_does_not_crash(tmp_path):
    path = tmp_path / "does-not-exist" / "translation_settings.json"
    settings = TranslationSettings(path=path)
    assert settings.default_engine == DEFAULT_CONFIG["default_engine"]
    assert settings.auto_engine_order == DEFAULT_CONFIG["auto_engine_order"]


def test_corrupted_settings_file_falls_back_to_defaults(tmp_path):
    path = tmp_path / "translation_settings.json"
    path.write_text("{not valid json::", encoding="utf-8")
    settings = TranslationSettings(path=path)
    assert settings.default_engine == DEFAULT_CONFIG["default_engine"]


def test_settings_file_with_wrong_shape_falls_back_to_defaults(tmp_path):
    path = tmp_path / "translation_settings.json"
    path.write_text(json.dumps(["not", "a", "dict"]), encoding="utf-8")
    settings = TranslationSettings(path=path)
    assert settings.default_engine == DEFAULT_CONFIG["default_engine"]


def test_invalid_persisted_engine_falls_back_safely_when_resolved(tmp_path):
    path = tmp_path / "translation_settings.json"
    path.write_text(json.dumps({"default_engine": "totally-made-up-engine"}), encoding="utf-8")
    settings = TranslationSettings(path=path)
    assert settings.default_engine == "totally-made-up-engine"  # stored as-is

    # A registry that only knows "googletrans" -- the manager must not
    # crash when the persisted default isn't actually available.
    class _Stub:
        name = "googletrans"
        display_name = "GoogleTrans"

        def is_available(self):
            return True

        async def translate(self, *a, **kw):
            return "ok"

    reg = ProviderRegistry()
    reg.register("googletrans", _Stub, display_name="GoogleTrans")

    mgr = TranslationManager(engine="default", settings=settings, registry=reg)
    assert mgr.resolved_engine_name() == "googletrans"


def test_admin_write_does_not_affect_already_constructed_managers(tmp_path):
    path = tmp_path / "translation_settings.json"
    settings = TranslationSettings(path=path)

    class _Stub:
        name = "x"
        display_name = "x"

        def is_available(self):
            return True

    reg = ProviderRegistry()
    reg.register("googletrans", _Stub, display_name="GoogleTrans")
    reg.register("translators-bing", _Stub, display_name="Translators - Bing")

    mgr = TranslationManager(engine="default", settings=settings, registry=reg)
    assert mgr.resolved_engine_name() == "googletrans"

    settings.set_default_engine("translators-bing")
    # Already-running job is unaffected; only a *new* manager sees it.
    assert mgr.resolved_engine_name() == "googletrans"
    new_mgr = TranslationManager(engine="default", settings=settings, registry=reg)
    assert new_mgr.resolved_engine_name() == "translators-bing"

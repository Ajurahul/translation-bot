from translation.registry import ProviderRegistry, registry
from translation.providers import ai_backend, libretranslate_backend, lingva_backend, translators_backend


def test_new_free_providers_are_registered_and_available():
    for engine_id in ("translators-reverso", "lingva", "libretranslate"):
        assert engine_id in registry.all_registered()
        assert registry.is_provider_available(engine_id), f"{engine_id} should be available with no key"


def test_ai_backends_are_registered_but_unavailable_without_a_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    reg = ProviderRegistry()
    ai_backend.register(reg)
    assert "ai-claude" in reg.all_registered()
    assert "ai-openai" in reg.all_registered()
    assert reg.is_provider_available("ai-claude") is False
    assert reg.is_provider_available("ai-openai") is False


def test_ai_backend_becomes_available_once_key_is_set(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    reg = ProviderRegistry()
    ai_backend.register(reg)
    assert reg.is_provider_available("ai-claude") is True


def test_ai_backends_are_not_in_the_default_auto_rotation():
    # Auto mode must never spend a configured key's money unless the
    # operator explicitly opts an AI engine into auto_engine_order
    # themselves -- see translation/providers/ai_backend.py.
    from translation.config import DEFAULT_CONFIG

    assert "ai-claude" not in DEFAULT_CONFIG["auto_engine_order"]
    assert "ai-openai" not in DEFAULT_CONFIG["auto_engine_order"]


def test_lingva_backend_builds_expected_url_and_parses_response(monkeypatch):
    backend = lingva_backend.LingvaBackend()
    captured = {}

    async def fake_request_json(method, url, **kwargs):
        captured["method"] = method
        captured["url"] = url
        return {"translation": "bonjour le monde"}

    monkeypatch.setattr(backend, "_request_json", fake_request_json)

    import asyncio

    result = asyncio.run(backend.translate("hello world", "en", "fr"))
    assert result == "bonjour le monde"
    assert captured["method"] == "GET"
    assert "/en/fr/" in captured["url"]


def test_libretranslate_backend_sends_expected_payload(monkeypatch):
    backend = libretranslate_backend.LibreTranslateBackend()
    captured = {}

    async def fake_request_json(method, url, **kwargs):
        captured["method"] = method
        captured["url"] = url
        captured["json_body"] = kwargs.get("json_body")
        return {"translatedText": "hola mundo"}

    monkeypatch.setattr(backend, "_request_json", fake_request_json)

    import asyncio

    result = asyncio.run(backend.translate("hello world", "en", "es"))
    assert result == "hola mundo"
    assert captured["method"] == "POST"
    assert captured["json_body"]["source"] == "en"
    assert captured["json_body"]["target"] == "es"
    assert "api_key" not in captured["json_body"]  # no key configured -> omitted


def test_libretranslate_backend_rejects_error_field(monkeypatch):
    import asyncio

    from translation.errors import TransientTranslationError

    backend = libretranslate_backend.LibreTranslateBackend()

    async def fake_request_json(method, url, **kwargs):
        return {"error": "something went wrong"}

    monkeypatch.setattr(backend, "_request_json", fake_request_json)

    try:
        asyncio.run(backend.translate("hi", "en", "fr"))
        assert False, "expected TransientTranslationError"
    except TransientTranslationError:
        pass


def test_translators_reverso_registered_with_stable_id_and_friendly_name():
    reg = ProviderRegistry()
    translators_backend.register(reg)
    assert "translators-reverso" in reg.all_registered()
    assert reg.get_display_name("translators-reverso") == "Translators - Reverso"

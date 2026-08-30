import pytest

from translation.base import TranslationBackend
from translation.registry import ProviderRegistry


class _StubBackend(TranslationBackend):
    name = "stub"
    display_name = "Stub"

    def __init__(self, available=True):
        self._available = available

    def is_available(self):
        return self._available

    async def translate(self, text, source_language, target_language):
        return f"[{target_language}] {text}"


def make_registry(available=True):
    reg = ProviderRegistry()
    reg.register("stub", lambda: _StubBackend(available), display_name="Stub Engine")
    return reg


def test_provider_lookup_returns_same_instance():
    reg = make_registry()
    a = reg.get_provider("stub")
    b = reg.get_provider("stub")
    assert a is b


def test_invalid_provider_raises_key_error():
    reg = make_registry()
    with pytest.raises(KeyError):
        reg.get_provider("does-not-exist")


def test_is_provider_available_true():
    reg = make_registry(available=True)
    assert reg.is_provider_available("stub") is True


def test_is_provider_available_false_when_backend_reports_unavailable():
    reg = make_registry(available=False)
    assert reg.is_provider_available("stub") is False


def test_is_provider_available_false_for_unknown_provider():
    reg = make_registry()
    assert reg.is_provider_available("nope") is False


def test_get_available_providers_filters_out_unavailable():
    reg = ProviderRegistry()
    reg.register("good", lambda: _StubBackend(True), display_name="Good")
    reg.register("bad", lambda: _StubBackend(False), display_name="Bad")
    assert reg.get_available_providers() == ["good"]


def test_get_display_name_falls_back_to_id():
    reg = make_registry()
    assert reg.get_display_name("stub") == "Stub Engine"
    assert reg.get_display_name("unknown-id") == "unknown-id"


def test_all_registered_lists_every_factory_regardless_of_availability():
    reg = ProviderRegistry()
    reg.register("good", lambda: _StubBackend(True))
    reg.register("bad", lambda: _StubBackend(False))
    assert set(reg.all_registered()) == {"good", "bad"}


def test_availability_check_never_raises_on_broken_factory():
    reg = ProviderRegistry()

    def _broken():
        raise RuntimeError("dependency exploded")

    reg.register("broken", _broken)
    assert reg.is_provider_available("broken") is False
    assert reg.get_available_providers() == []

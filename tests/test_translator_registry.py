from translator import registry


def test_all_expected_engines_registered():
    keys = {spec.key for spec in registry.all_specs()}
    assert {"googletrans", "deep_translator", "bing", "baidu", "alibaba",
            "youdao", "mymemory", "libretranslate"}.issubset(keys)


def test_get_spec_unknown_engine_returns_none():
    assert registry.get_spec("not_a_real_engine") is None


def test_no_key_engines_are_categorized_correctly():
    for key in ("googletrans", "deep_translator", "bing", "baidu", "alibaba", "youdao", "mymemory"):
        spec = registry.get_spec(key)
        assert spec is not None
        assert spec.api_key_tier == "none"


def test_libretranslate_is_categorized_as_free_tier_with_key():
    spec = registry.get_spec("libretranslate")
    assert spec.api_key_tier == "free_tier"
    assert spec.api_key_env == "LIBRETRANSLATE_API_KEY"


def test_no_paid_only_engines_are_registered():
    # Rule 9: never add a paid-only service.
    paid_only_examples = {"deepl", "sysTran", "languageWire", "cloudTranslation", "lara", "modernMt"}
    keys = {spec.key for spec in registry.all_specs()}
    assert keys.isdisjoint(paid_only_examples)


def test_is_engine_available_false_for_missing_dependency(monkeypatch):
    class FakeUnavailableBackend:
        def is_available(self):
            return False

    monkeypatch.setattr(registry, "get_spec", lambda key: registry.EngineSpec(
        key="fake", display_name="Fake", factory=FakeUnavailableBackend,
        api_key_tier="none",
    ) if key == "fake" else None)
    registry.reset_availability_cache()
    assert registry.is_engine_available("fake") is False


def test_is_engine_available_true_when_backend_reports_available(monkeypatch):
    class FakeAvailableBackend:
        def is_available(self):
            return True

    monkeypatch.setattr(registry, "get_spec", lambda key: registry.EngineSpec(
        key="fake2", display_name="Fake2", factory=FakeAvailableBackend,
        api_key_tier="none",
    ) if key == "fake2" else None)
    registry.reset_availability_cache()
    assert registry.is_engine_available("fake2") is True


def test_available_specs_excludes_unavailable(monkeypatch):
    good = registry.EngineSpec(
        key="good", display_name="Good",
        factory=lambda: type("B", (), {"is_available": lambda self: True})(),
        api_key_tier="none",
    )
    bad = registry.EngineSpec(
        key="bad", display_name="Bad",
        factory=lambda: type("B", (), {"is_available": lambda self: False})(),
        api_key_tier="none",
    )
    monkeypatch.setattr(registry, "ALL_SPECS", [good, bad])
    monkeypatch.setattr(registry, "_SPECS_BY_KEY", {"good": good, "bad": bad})
    registry.reset_availability_cache()
    keys = {spec.key for spec in registry.available_specs()}
    assert keys == {"good"}


def test_get_backend_unknown_engine_raises_keyerror():
    try:
        registry.get_backend("definitely_not_registered")
        assert False, "expected KeyError"
    except KeyError:
        pass

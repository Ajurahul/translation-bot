import asyncio
import typing as t

import pytest

from translator import registry
from translator.base import TranslationBackend, TranslationFailedError
from translator.manager import EngineChoice, TranslationManager


class FakeBackend(TranslationBackend):
    """Configurable fake engine for tests.

    `fail_calls` is a set of 1-indexed call numbers on which this backend
    should raise; every other call succeeds.
    """

    def __init__(self, key: str, fail_calls: t.Optional[t.Set[int]] = None,
                 exc_factory=None, available: bool = True):
        self._key = key
        self.fail_calls = fail_calls or set()
        self.exc_factory = exc_factory or (lambda: RuntimeError("HTTP 500 server error"))
        self.calls = 0
        self.call_texts = []
        self._available = available

    @property
    def name(self) -> str:
        return self._key

    def is_available(self) -> bool:
        return self._available

    async def translate(self, text: str, source_language: str, target_language: str) -> str:
        self.calls += 1
        self.call_texts.append(text)
        if self.calls in self.fail_calls:
            raise self.exc_factory()
        return f"[{self._key}] {text}"


def _wire(monkeypatch, backends: t.Dict[str, FakeBackend]):
    specs = [
        registry.EngineSpec(key=key, display_name=key.title(), factory=(lambda b=b: b),
                             api_key_tier="none")
        for key, b in backends.items()
    ]
    monkeypatch.setattr(registry, "ALL_SPECS", specs)
    monkeypatch.setattr(registry, "_SPECS_BY_KEY", {s.key: s for s in specs})
    monkeypatch.setattr(registry, "get_backend", lambda key: backends[key])
    registry.reset_availability_cache()


def run(coro):
    return asyncio.run(coro)


# --- Engine selection ------------------------------------------------------

def test_engine_choice_default_resolves_persisted_engine(monkeypatch):
    monkeypatch.setattr("translator.manager.get_default_engine", lambda: "bing")
    choice = EngineChoice("Default")
    assert choice.is_auto is False
    assert choice.engine_key == "bing"
    assert choice.resolved_from_default is True


def test_engine_choice_auto():
    choice = EngineChoice("Auto")
    assert choice.is_auto is True
    assert choice.engine_key is None


def test_engine_choice_explicit_engine_is_lowercased():
    choice = EngineChoice("GoogleTrans")
    assert choice.is_auto is False
    assert choice.engine_key == "googletrans"
    assert choice.resolved_from_default is False


def test_engine_choice_none_or_missing_defaults_to_default_mode(monkeypatch):
    monkeypatch.setattr("translator.manager.get_default_engine", lambda: "googletrans")
    choice = EngineChoice(None)
    assert choice.is_auto is False
    assert choice.engine_key == "googletrans"


def test_explicit_invalid_engine_fails_without_calling_anything(monkeypatch):
    g = FakeBackend("googletrans")
    _wire(monkeypatch, {"googletrans": g})
    mgr = TranslationManager(engine_mode="not_a_real_engine")
    with pytest.raises(TranslationFailedError):
        run(mgr.translate_one("hello", "auto", "en"))
    assert g.calls == 0


def test_explicit_engine_with_missing_dependency_fails_immediately(monkeypatch):
    g = FakeBackend("googletrans", available=False)
    _wire(monkeypatch, {"googletrans": g})
    mgr = TranslationManager(engine_mode="googletrans")
    with pytest.raises(TranslationFailedError):
        run(mgr.translate_one("hello", "auto", "en"))
    assert g.calls == 0


# --- Explicit engine: no silent fallback ------------------------------------

def test_explicit_engine_success():
    pass


def test_explicit_engine_selected_and_it_fails_raises_and_does_not_use_others(monkeypatch):
    g = FakeBackend("googletrans", fail_calls={1, 2, 3, 4})  # always fails within retry budget
    d = FakeBackend("deep_translator")
    _wire(monkeypatch, {"googletrans": g, "deep_translator": d})

    mgr = TranslationManager(engine_mode="googletrans", max_retries=2, retry_delays=[0, 0], request_delay=0)
    with pytest.raises(TranslationFailedError) as exc_info:
        run(mgr.translate_one("hello world this is long enough to not be split maybe", "auto", "en"))

    assert exc_info.value.engine == "googletrans"
    assert d.calls == 0  # never silently switched engines


def test_explicit_engine_succeeds_normally(monkeypatch):
    g = FakeBackend("googletrans")
    _wire(monkeypatch, {"googletrans": g})
    mgr = TranslationManager(engine_mode="googletrans", request_delay=0)
    result = run(mgr.translate_one("hello", "auto", "en"))
    assert result == "[googletrans] hello"


# --- Auto mode ---------------------------------------------------------------

def test_auto_first_engine_succeeds(monkeypatch):
    g = FakeBackend("googletrans")
    d = FakeBackend("deep_translator")
    _wire(monkeypatch, {"googletrans": g, "deep_translator": d})
    mgr = TranslationManager(engine_mode="auto", request_delay=0)
    result = run(mgr.translate_one("hi", "auto", "en"))
    assert result == "[googletrans] hi"
    assert d.calls == 0


def test_auto_falls_through_to_second_engine(monkeypatch):
    g = FakeBackend("googletrans", fail_calls={1})
    d = FakeBackend("deep_translator")
    _wire(monkeypatch, {"googletrans": g, "deep_translator": d})
    mgr = TranslationManager(engine_mode="auto", request_delay=0)
    result = run(mgr.translate_one("hi", "auto", "en"))
    assert result == "[deep_translator] hi"


def test_auto_falls_through_two_failures_to_third_engine(monkeypatch):
    g = FakeBackend("googletrans", fail_calls={1})
    d = FakeBackend("deep_translator", fail_calls={1})
    b = FakeBackend("bing")
    _wire(monkeypatch, {"googletrans": g, "deep_translator": d, "bing": b})
    mgr = TranslationManager(engine_mode="auto", request_delay=0)
    result = run(mgr.translate_one("hi", "auto", "en"))
    assert result == "[bing] hi"


def test_auto_sticks_with_working_engine_for_next_chunk(monkeypatch):
    g = FakeBackend("googletrans", fail_calls={1})
    d = FakeBackend("deep_translator")
    _wire(monkeypatch, {"googletrans": g, "deep_translator": d})
    mgr = TranslationManager(engine_mode="auto", request_delay=0, max_concurrency=1)

    first = run(mgr.translate_one("chunk1", "auto", "en"))
    second = run(mgr.translate_one("chunk2", "auto", "en"))

    assert first == "[deep_translator] chunk1"
    assert second == "[deep_translator] chunk2"
    # googletrans must only have been tried once (on the first chunk) --
    # NOT retried again on the second chunk.
    assert g.calls == 1
    assert d.calls == 2


def test_auto_resets_and_retries_when_all_engines_fail_then_recover(monkeypatch):
    # Both engines fail once each (their very first call), then succeed
    # on the retry after the pool resets.
    g = FakeBackend("googletrans", fail_calls={1})
    d = FakeBackend("deep_translator", fail_calls={1})
    _wire(monkeypatch, {"googletrans": g, "deep_translator": d})
    mgr = TranslationManager(engine_mode="auto", request_delay=0, max_engine_resets=2)

    result = run(mgr.translate_one("hi", "auto", "en"))
    assert result is not None
    assert "hi" in result


def test_auto_all_engines_fail_permanently_raises_after_bounded_resets(monkeypatch):
    g = FakeBackend("googletrans", fail_calls=set(range(1, 50)))
    d = FakeBackend("deep_translator", fail_calls=set(range(1, 50)))
    _wire(monkeypatch, {"googletrans": g, "deep_translator": d})
    mgr = TranslationManager(engine_mode="auto", request_delay=0, max_engine_resets=2)

    with pytest.raises(TranslationFailedError) as exc_info:
        run(mgr.translate_one("hi", "auto", "en"))
    assert exc_info.value.engine == "auto"
    # bounded: not an infinite loop -- must have stopped after the
    # configured number of resets rather than retrying forever.
    assert g.calls < 20
    assert d.calls < 20


def test_failed_engines_do_not_remain_permanently_disabled(monkeypatch):
    g = FakeBackend("googletrans", fail_calls={1})
    d = FakeBackend("deep_translator")
    _wire(monkeypatch, {"googletrans": g, "deep_translator": d})
    mgr = TranslationManager(engine_mode="auto", request_delay=0)

    run(mgr.translate_one("hi", "auto", "en"))
    assert "googletrans" in mgr._failed_engines

    mgr.reset_failed_engines()
    assert "googletrans" not in mgr._failed_engines
    assert "googletrans" in mgr.get_available_engines()


# --- HTTP status / failure-type handling -------------------------------------

@pytest.mark.parametrize("message", [
    "HTTP 500 Internal Server Error",
    "HTTP 429 Too Many Requests",
    "HTTP 503 Service Unavailable",
    "Request timed out",
    "connection refused",
])
def test_transient_failures_are_retried_on_same_explicit_engine(monkeypatch, message):
    g = FakeBackend("googletrans", fail_calls={1},
                     exc_factory=lambda m=message: RuntimeError(m))
    _wire(monkeypatch, {"googletrans": g})
    mgr = TranslationManager(engine_mode="googletrans", max_retries=2, retry_delays=[0, 0], request_delay=0)
    result = run(mgr.translate_one("hi", "auto", "en"))
    assert result == "[googletrans] hi"
    assert g.calls == 2  # failed once, retried and succeeded


def test_none_response_is_treated_as_failure(monkeypatch):
    class NoneBackend(TranslationBackend):
        name = "googletrans"

        def is_available(self):
            return True

        async def translate(self, text, source_language, target_language):
            return None

    _wire(monkeypatch, {"googletrans": NoneBackend()})
    mgr = TranslationManager(engine_mode="googletrans", max_retries=0, request_delay=0)
    with pytest.raises(TranslationFailedError):
        run(mgr.translate_one("hi", "auto", "en"))


def test_error_page_response_is_rejected_not_returned(monkeypatch):
    class ErrorPageBackend(TranslationBackend):
        name = "googletrans"

        def is_available(self):
            return True

        async def translate(self, text, source_language, target_language):
            return "Error 500: Server Error. That's an error. Please try again later."

    _wire(monkeypatch, {"googletrans": ErrorPageBackend()})
    mgr = TranslationManager(engine_mode="googletrans", max_retries=0, request_delay=0)
    with pytest.raises(TranslationFailedError):
        run(mgr.translate_one("hi", "auto", "en"))


# --- Concurrency --------------------------------------------------------------

def test_concurrency_is_bounded(monkeypatch):
    max_in_flight = 0
    current_in_flight = 0
    lock = asyncio.Lock()

    class SlowBackend(TranslationBackend):
        name = "googletrans"

        def is_available(self):
            return True

        async def translate(self, text, source_language, target_language):
            nonlocal max_in_flight, current_in_flight
            async with lock:
                current_in_flight += 1
                max_in_flight = max(max_in_flight, current_in_flight)
            await asyncio.sleep(0.02)
            async with lock:
                current_in_flight -= 1
            return f"[googletrans] {text}"

    _wire(monkeypatch, {"googletrans": SlowBackend()})
    mgr = TranslationManager(engine_mode="googletrans", max_concurrency=3, request_delay=0)
    texts = [f"chunk{i}" for i in range(12)]
    results = run(mgr.translate_many(texts, "auto", "en"))

    assert len(results) == 12
    assert max_in_flight <= 3


# --- Chunk recovery ------------------------------------------------------------

def test_large_chunk_is_split_and_recovered(monkeypatch):
    # Fails on whole text, succeeds once split below min_recoverable_chunk_chars.
    class SplitAwareBackend(TranslationBackend):
        name = "googletrans"

        def is_available(self):
            return True

        async def translate(self, text, source_language, target_language):
            if len(text) > 20:
                raise RuntimeError("HTTP 500 server error")
            return f"[ok]{text}"

    _wire(monkeypatch, {"googletrans": SplitAwareBackend()})
    mgr = TranslationManager(
        engine_mode="googletrans", max_retries=0, request_delay=0,
        min_recoverable_chunk_chars=10,
    )
    text = "a" * 50
    result = run(mgr.translate_one(text, "auto", "en"))
    assert "[ok]" in result

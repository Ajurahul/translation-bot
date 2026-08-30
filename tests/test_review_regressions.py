"""Regression tests for bugs found during the deep code review.

Each test below reproduces a specific bug that existed before this
review's fixes; see the inline comments in the corresponding source files
(translator/manager.py, translator/settings.py, translator/base.py,
translator/backends/libretranslate_engine.py, utils/translate.py,
cogs/translation.py) for the full root-cause writeup.
"""
import asyncio
import concurrent.futures
import threading

import pytest

from translator import registry
from translator.base import TranslationBackend, classify_exception
from translator.manager import TranslationManager
from utils.translate import Translator
from unittest.mock import MagicMock


def run(coro):
    return asyncio.run(coro)


class FakeBackend(TranslationBackend):
    def __init__(self, key, fail_calls=None):
        self._key = key
        self.fail_calls = fail_calls or set()
        self.calls = 0

    @property
    def name(self):
        return self._key

    def is_available(self):
        return True

    async def translate(self, text, source_language, target_language):
        self.calls += 1
        if self.calls in self.fail_calls:
            raise RuntimeError("HTTP 500 server error")
        return f"[{self._key}] {text}"


def _wire(monkeypatch, backends):
    specs = [
        registry.EngineSpec(key=key, display_name=key.title(), factory=(lambda b=b: b),
                             api_key_tier="none")
        for key, b in backends.items()
    ]
    monkeypatch.setattr(registry, "ALL_SPECS", specs)
    monkeypatch.setattr(registry, "_SPECS_BY_KEY", {s.key: s for s in specs})
    monkeypatch.setattr(registry, "get_backend", lambda key: backends[key])
    registry.reset_availability_cache()


# --- BUG: Translator recreated per chunk-batch reset sticky-engine state ---

def test_sticky_engine_survives_across_multiple_translator_instances_sharing_one_manager(monkeypatch):
    """Reproduces the large-file bug: cogs/translation.py builds a *new*
    `Translator` per 1000-line chunk-batch (and `del`s the old one) to
    bound memory. Before the fix, each new Translator lazily built its
    own private TranslationManager, so Auto mode's "remember which engine
    works" state silently reset every chunk-batch. The fix is passing one
    shared TranslationManager into every Translator instance for the job.
    """
    g = FakeBackend("googletrans", fail_calls={1})  # fails once, then would work
    d = FakeBackend("deep_translator")
    _wire(monkeypatch, {"googletrans": g, "deep_translator": d})

    bot = MagicMock()
    shared_manager = TranslationManager(engine_mode="auto", request_delay=0)

    # Chunk-batch 1: a fresh Translator (as the large-file loop creates).
    tr1 = Translator(bot, user=1, language="en", engine_mode="auto",
                      translation_manager=shared_manager)
    result1 = tr1._translate_batch_with_retry(["chunk-a"])
    del tr1  # mirrors `del translate` in cogs/translation.py

    # Chunk-batch 2: another brand new Translator instance, same job.
    tr2 = Translator(bot, user=1, language="en", engine_mode="auto",
                      translation_manager=shared_manager)
    result2 = tr2._translate_batch_with_retry(["chunk-b"])

    assert result1 == ["[deep_translator] chunk-a"]
    assert result2 == ["[deep_translator] chunk-b"]
    # googletrans must have been tried exactly once (chunk-batch 1) and
    # never retried on chunk-batch 2 -- proving the failure was
    # remembered *across* the two Translator instances.
    assert g.calls == 1
    assert d.calls == 2


def test_without_shared_manager_each_translator_gets_isolated_state(monkeypatch):
    """Sanity check for the *other* half of the contract: a caller that
    does NOT pass `translation_manager` (every existing caller, and any
    single-Translator-per-job caller) still gets a private manager per
    instance, unchanged from before this fix."""
    g = FakeBackend("googletrans")
    _wire(monkeypatch, {"googletrans": g})
    bot = MagicMock()

    tr1 = Translator(bot, user=1, language="en", engine_mode="googletrans")
    tr2 = Translator(bot, user=1, language="en", engine_mode="googletrans")
    assert tr1._get_manager() is not tr2._get_manager()


# --- BUG: real concurrency was uncoupled from max_concurrency -------------

def test_concurrency_bounded_across_real_os_threads(monkeypatch):
    """Reproduces the concurrency-multiplication bug: utils.translate.
    Translator drives translation from a ThreadPoolExecutor with 4-6
    real OS-thread workers, each calling `translate_many` with exactly
    ONE item. Before the fix, the manager's asyncio.Semaphore never saw
    more than 1 concurrent task (it's scoped per-call), so the *actual*
    concurrent request count was bounded only by the thread pool's own
    worker count -- completely uncoupled from `max_concurrency`."""
    max_in_flight = 0
    current_in_flight = 0
    lock = threading.Lock()

    class SlowBackend(TranslationBackend):
        name = "googletrans"

        def is_available(self):
            return True

        async def translate(self, text, source_language, target_language):
            nonlocal max_in_flight, current_in_flight
            with lock:
                current_in_flight += 1
                max_in_flight = max(max_in_flight, current_in_flight)
            await asyncio.sleep(0.05)
            with lock:
                current_in_flight -= 1
            return f"[googletrans] {text}"

    _wire(monkeypatch, {"googletrans": SlowBackend()})
    mgr = TranslationManager(engine_mode="googletrans", max_concurrency=3, request_delay=0)

    def worker(chapter):
        return asyncio.run(mgr.translate_many([chapter], "auto", "en"))

    # 6 real OS threads "hammering" the same manager -- like
    # Translator.get_no_of_workers() returns for a mid-size novel.
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as ex:
        futures = [ex.submit(worker, f"chunk{i}") for i in range(18)]
        [f.result() for f in concurrent.futures.as_completed(futures)]

    assert max_in_flight <= 3


# --- BUG: unguarded read of shared state in auto-mode engine switch -------

def test_mark_engine_success_returns_previous_engine_atomically():
    mgr = TranslationManager(engine_mode="auto")
    assert mgr.mark_engine_success("googletrans") is None
    assert mgr.mark_engine_success("deep_translator") == "googletrans"
    assert mgr.mark_engine_success("deep_translator") == "deep_translator"


# --- BUG: persisted default engine was never validated against the registry

def test_invalid_persisted_default_falls_back_safely(monkeypatch, tmp_path):
    from translator import settings

    settings_dir = tmp_path / "config"
    settings_path = settings_dir / "translation_settings.json"
    monkeypatch.setattr(settings, "_SETTINGS_DIR", str(settings_dir))
    monkeypatch.setattr(settings, "_SETTINGS_PATH", str(settings_path))

    settings.set_default_engine("this_engine_does_not_exist_anymore")
    # Must not return the stale/garbage value -- must fall back safely,
    # exactly like a missing/corrupted file.
    assert settings.get_default_engine() == settings.FALLBACK_DEFAULT_ENGINE


def test_valid_persisted_default_is_returned_unchanged(monkeypatch, tmp_path):
    from translator import settings

    settings_dir = tmp_path / "config"
    settings_path = settings_dir / "translation_settings.json"
    monkeypatch.setattr(settings, "_SETTINGS_DIR", str(settings_dir))
    monkeypatch.setattr(settings, "_SETTINGS_PATH", str(settings_path))

    settings.set_default_engine("bing")  # a real, registered engine
    assert settings.get_default_engine() == "bing"


# --- BUG: classify_exception missed httpx/requests-style status codes ----

def test_classify_exception_reads_status_from_response_attribute():
    class FakeResponse:
        status_code = 429

    class FakeHttpxError(Exception):
        def __init__(self):
            super().__init__("boom")
            self.response = FakeResponse()

    assert classify_exception(FakeHttpxError()) is True  # 429 -> retryable

    class FakeResponse400:
        status_code = 400

    class FakeHttpxError400(Exception):
        def __init__(self):
            super().__init__("boom")
            self.response = FakeResponse400()

    assert classify_exception(FakeHttpxError400()) is False  # 400 -> not retryable


# --- BUG: LibreTranslate opened a new aiohttp session per call -----------

def test_libretranslate_reuses_one_session_across_calls():
    from translator.backends.libretranslate_engine import LibreTranslateBackend

    backend = LibreTranslateBackend()

    async def get_two_sessions():
        s1 = await backend._get_session()
        s2 = await backend._get_session()
        await s1.close()
        return s1, s2

    s1, s2 = run(get_two_sessions())
    assert s1 is s2

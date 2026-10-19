"""Regression tests for the translation-bug batch:

  * crawled titles coming back as "None"
  * "'AUTO' IS AN INVALID SOURCE LANGUAGE ..." (MyMemory) landing in the text
  * filter_error_text deleting real text ("terror" -> "t")
  * failed chunks losing their original text / IndexError at job end
  * N worker threads each launching their own Auto race
  * quota failures disabling an engine for the whole process lifetime
  * stale persisted settings pinning the old (slow) limits
"""
import asyncio
import json
import threading
import time
from unittest.mock import MagicMock

import pytest

from translation import health
from translation.base import ProviderCapabilities, TranslationBackend
from translation.config import SCHEMA_VERSION, TranslationSettings
from translation.errors import (
    PermanentTranslationError,
    TransientTranslationError,
    filter_error_text,
    is_none_like,
    is_provider_request_error,
)
from translation.manager import TranslationManager
from translation.registry import ProviderRegistry
from translation.validation import validate_translation, validate_translation_batch

MYMEMORY_SOFT_ERROR = (
    "'AUTO' IS AN INVALID SOURCE LANGUAGE . EXAMPLE: LANGPAIR=EN|IT USING 2 LETTER "
    "ISO OR RFC3066 LIKE ZH-CN. ALMOST ALL LANGUAGES SUPPORTED BUT SOME MAY HAVE NO CONTENT"
)


# -- helpers ---------------------------------------------------------
class FakeSettings:
    def __init__(self, order=("a", "b"), retry_delays=()):
        self.default_engine = order[0]
        self._order = list(order)
        self._delays = list(retry_delays)
        self.max_concurrency = 8
        self.requests_per_minute = 1_000_000
        self.provider_concurrency = {}
        self.provider_requests_per_minute = {}
        self.min_recoverable_chunk_chars = 10

    @property
    def auto_engine_order(self):
        return list(self._order)

    @property
    def retry_delays(self):
        return list(self._delays)


class Recorder(TranslationBackend):
    """Backend that records the source language it was called with."""

    def __init__(self, name, result="ok", supports_auto=True, delay=0.0, fail=None):
        self.name = name
        self.display_name = name
        self.capabilities = ProviderCapabilities(supports_auto_source=supports_auto)
        self.result = result
        self.delay = delay
        self.fail = fail
        self.sources = []
        self.calls = 0

    def is_available(self):
        return True

    async def translate(self, text, source_language, target_language):
        self.calls += 1
        self.sources.append(source_language)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.fail is not None:
            raise self.fail
        return self.result


def make_registry(*backends):
    reg = ProviderRegistry()
    for b in backends:
        reg.register(b.name, lambda b=b: b, display_name=b.name)
    return reg


@pytest.fixture(autouse=True)
def _clean_health():
    health.clear_all()
    yield
    health.clear_all()


@pytest.fixture
def detect_as(monkeypatch):
    def _set(code):
        async def fake_detect(text=None, samples=None):
            return code

        monkeypatch.setattr("translation.manager.detect_language_code", fake_detect)

    return _set


# -- 1. "None" titles -------------------------------------------------
def test_is_none_like():
    assert is_none_like(None)
    assert is_none_like("None") and is_none_like(" none ") and is_none_like("null")
    assert not is_none_like("Nonexistent Hero")


def test_validation_rejects_the_string_none_for_real_input():
    with pytest.raises(PermanentTranslationError):
        validate_translation("我的修仙之路", "None")
    with pytest.raises(PermanentTranslationError):
        validate_translation_batch(["我的修仙之路"], ["None"])


def test_validation_still_allows_a_legitimate_none_for_a_tiny_input():
    # "无" really does translate to "None" -- only substantial inputs are held to this.
    assert validate_translation("无", "None") == "None"


@pytest.mark.parametrize("bad", [None, "", "  ", "None", "null", MYMEMORY_SOFT_ERROR])
def test_usable_title_rejects_junk(bad):
    from utils.translate import Translator

    assert Translator._usable_title("我的修仙之路", bad) is None


def test_usable_title_rejects_untranslated_echo_but_accepts_real_translation():
    from utils.translate import Translator

    assert Translator._usable_title("我的修仙之路", "我的修仙之路") is None
    assert Translator._usable_title("我的修仙之路", " My Path of Cultivation ") == "My Path of Cultivation"
    # An already-ASCII title legitimately "translates" to itself.
    assert Translator._usable_title("Solo Leveling", "Solo Leveling") == "Solo Leveling"


def test_title_falls_back_to_the_next_engine_when_auto_gives_junk(monkeypatch):
    from utils import translate as tr

    class FakeManager:
        def __init__(self, engine="auto"):
            self.state = MagicMock(failed_engines={"googletrans"}, active_engine=None)
            self.via_calls = []

        async def translate(self, text, source, target):
            return "None"  # the old failure mode

        async def translate_via(self, engine, text, source, target, delays=None):
            self.via_calls.append(engine)
            if engine == "deep-google":
                raise tr.TranslationError("down")
            return "My Path of Cultivation"

    monkeypatch.setattr(tr, "TranslationManager", FakeManager)
    monkeypatch.setattr(tr._translation_settings.__class__, "auto_engine_order",
                        property(lambda self: ["googletrans", "deep-google", "lingva"]))
    monkeypatch.setattr(tr.translation_registry, "is_provider_available", lambda n: True)
    monkeypatch.setattr(tr.translation_registry, "all_registered",
                        lambda: ["googletrans", "deep-google", "lingva"])

    result = asyncio.run(tr.Translator.atranslate_title("我的修仙之路"))
    assert result == "My Path of Cultivation"


def test_title_returns_none_not_the_word_none_when_every_engine_fails(monkeypatch):
    from utils import translate as tr

    class DeadManager:
        def __init__(self, engine="auto"):
            self.state = MagicMock(failed_engines=set(), active_engine=None)

        async def translate(self, *a):
            raise tr.TranslationError("all down")

        async def translate_via(self, *a, **k):
            raise tr.TranslationError("down")

    monkeypatch.setattr(tr, "TranslationManager", DeadManager)
    monkeypatch.setattr(tr.translation_registry, "is_provider_available", lambda n: True)
    assert asyncio.run(tr.Translator.atranslate_title("我的修仙之路")) is None
    assert asyncio.run(tr.Translator.atranslate_title("")) is None


# -- 2. "auto is an invalid source language" ---------------------------
def test_mymemory_soft_error_text_is_detected():
    assert is_provider_request_error(MYMEMORY_SOFT_ERROR)
    assert is_provider_request_error("PLEASE SELECT TWO DISTINCT LANGUAGES")
    assert not is_provider_request_error("The invalid source of the rumor was never found.")


def test_validation_never_accepts_the_soft_error_as_a_translation():
    with pytest.raises(PermanentTranslationError):
        validate_translation("안녕하세요 여러분", MYMEMORY_SOFT_ERROR)
    with pytest.raises(PermanentTranslationError):
        validate_translation_batch(["안녕하세요 여러분"], [MYMEMORY_SOFT_ERROR])


def test_real_backends_declare_they_cannot_take_auto():
    from translation.registry import registry

    for name in ("deep-mymemory", "translators-mymemory", "deepl", "microsoft", "papago"):
        assert registry.get_provider(name).capabilities.supports_auto_source is False, name
    for name in ("googletrans", "deep-google", "libretranslate", "lingva", "baidu"):
        assert registry.get_provider(name).capabilities.supports_auto_source is True, name


async def test_engine_without_auto_support_gets_a_detected_source(detect_as):
    detect_as("ko")
    mm = Recorder("a", result="hello", supports_auto=False)
    mgr = TranslationManager(engine="a", settings=FakeSettings(("a",)), registry=make_registry(mm))
    assert await mgr.translate("안녕하세요 여러분", "auto", "en") == "hello"
    assert mm.sources == ["ko"]  # never "auto"


async def test_engine_with_auto_support_still_gets_auto(detect_as):
    detect_as("ko")
    g = Recorder("a", result="hello", supports_auto=True)
    mgr = TranslationManager(engine="a", settings=FakeSettings(("a",)), registry=make_registry(g))
    await mgr.translate("안녕하세요 여러분", "auto", "en")
    assert g.sources == ["auto"]


async def test_explicit_source_is_never_overridden(detect_as):
    detect_as("ko")
    mm = Recorder("a", supports_auto=False)
    mgr = TranslationManager(engine="a", settings=FakeSettings(("a",)), registry=make_registry(mm))
    await mgr.translate("hola amigos míos", "es", "en")
    assert mm.sources == ["es"]


async def test_undetectable_source_fails_that_engine_not_the_job_in_auto(detect_as):
    detect_as("NA")
    mm = Recorder("a", supports_auto=False)
    good = Recorder("b", result="translated", supports_auto=True, delay=0.02)
    mgr = TranslationManager(engine="auto", settings=FakeSettings(("a", "b")), registry=make_registry(mm, good))
    assert await mgr.translate("12345 67890", "auto", "en") == "translated"
    assert mm.calls == 0  # never even sent a bad "auto" request
    assert mgr.state.active_engine == "b"


async def test_already_target_language_text_is_passed_through_for_default_engine(detect_as):
    # e.g. the English "(for more novels ... join: discord...)" line inserted
    # into a Korean->English job when MyMemory is the chosen engine.
    detect_as("en")
    mm = Recorder("a", supports_auto=False)
    mgr = TranslationManager(engine="a", settings=FakeSettings(("a",)), registry=make_registry(mm))
    assert await mgr.translate("for more novels join us", "auto", "en") == "for more novels join us"
    assert mm.calls == 0


async def test_already_target_language_cannot_win_an_auto_race_instantly(detect_as):
    detect_as("en")
    mm = Recorder("a", supports_auto=False)
    good = Recorder("b", result="from-b", supports_auto=True, delay=0.02)
    mgr = TranslationManager(engine="auto", settings=FakeSettings(("a", "b")), registry=make_registry(mm, good))
    assert await mgr.translate("already english text", "auto", "en") == "from-b"
    assert mgr.state.active_engine == "b"


# -- 3. filter_error_text ----------------------------------------------
@pytest.mark.parametrize(
    "text",
    [
        "The villagers fled in terror.\nHe drew his sword.\nThe end.",
        "He felt terror. More story follows that must survive.",
        "Terror gripped her.\nनमस्ते rest of the story",
        "ErrorBoundary is a React concept.",
    ],
)
def test_filter_error_text_leaves_normal_prose_alone(text):
    assert filter_error_text(text) == text


def test_filter_error_text_still_strips_real_error_pages():
    page = "Error 500 (Server Error)!!1\nThat's an error. There was an error. Please try again later.\nReal text"
    assert filter_error_text(page) == "Real text"


# -- 4. chunk recovery / story assembly --------------------------------
def _translator(bot=None):
    from utils.translate import Translator

    bot = bot or MagicMock()
    bot.translator = {}
    return Translator(bot, user=1, language="english", engine="auto")


def test_failed_chunk_keeps_its_original_text_behind_a_marker(monkeypatch):
    tr = _translator()
    monkeypatch.setattr(tr, "_translate_batch_with_retry", MagicMock(side_effect=RuntimeError("boom")))
    text = "원문 " * 50
    num, parts = tr.translate([text], 7)
    assert num == 7
    joined = "".join(parts)
    assert text in joined
    assert "couldn't translate this part" in joined


def test_engines_down_does_not_waste_time_splitting(monkeypatch):
    from translation.errors import AllEnginesFailedError

    tr = _translator()
    calls = []

    def boom(chapter):
        calls.append(chapter)
        err = RuntimeError("all down")
        err.__cause__ = AllEnginesFailedError("x")
        raise err

    monkeypatch.setattr(tr, "_translate_batch_with_retry", boom)
    tr.translate(["x" * 500], 0)
    assert len(calls) == 1  # one attempt, no split/re-send


def test_content_problem_is_retried_once_per_half(monkeypatch):
    tr = _translator()
    big = ("word " * 100).strip()

    def flaky(chapter):
        if len(chapter[0]) > 300:
            raise RuntimeError("request too large")
        return [chapter[0].upper()]

    monkeypatch.setattr(tr, "_translate_batch_with_retry", flaky)
    num, parts = tr.translate([big], 0)
    joined = "".join(parts)
    assert "couldn't translate" not in joined
    assert joined.replace(" ", "") == big.upper().replace(" ", "")


def test_start_joins_every_part_and_survives_an_empty_result():
    import asyncio as aio

    tr = _translator()
    # chunk 1 failed (marker + original), chunk 2 normal, chunk 3 came back empty
    tr.order = {
        0: ["A"],
        1: ["\n\n--->couldn't translate this part\n", "ORIGINAL"],
        2: [],
        3: ["D"],
    }

    class _Loop:
        async def run_in_executor(self, *_):
            return None

    tr.bot.loop = _Loop()
    story = aio.run(tr.start(["x"], 1))
    assert story == "A\n\n--->couldn't translate this part\nORIGINALD"


def test_workers_reuse_one_event_loop_each_and_close_them(monkeypatch):
    from utils.translate import Translator

    tr = _translator()
    seen = {}

    async def probe(chapter):
        seen.setdefault(threading.get_ident(), set()).add(id(asyncio.get_running_loop()))
        await asyncio.sleep(0.001)
        return [str(c).upper() for c in chapter]

    monkeypatch.setattr(tr, "_atranslate_batch_with_retry", probe)
    monkeypatch.setattr(Translator, "get_no_of_workers", staticmethod(lambda *a: 3))
    tr.translates([f"chunk{i}" for i in range(30)], 3)

    assert len(tr.order) == 30
    assert [tr.order[i][0] for i in range(30)] == [f"CHUNK{i}" for i in range(30)]
    # Every worker thread used exactly ONE loop for all of its chunks.
    assert seen and all(len(loops) == 1 for loops in seen.values())


# -- 5. Auto discovery across worker threads ---------------------------
def test_many_workers_sharing_one_manager_trigger_only_one_race():
    slow_loser = Recorder("a", delay=0.15, fail=TransientTranslationError("down"))
    winner = Recorder("b", result="ok", delay=0.02)
    reg = make_registry(slow_loser, winner)
    mgr = TranslationManager(engine="auto", settings=FakeSettings(("a", "b")), registry=reg)

    results = []

    def worker():
        results.append(asyncio.run(mgr.translate("hello world", "auto", "fr")))

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for th in threads:
        th.start()
    for th in threads:
        th.join(timeout=10)

    assert results == ["ok"] * 6
    # Without the discovery lock each of the 6 threads raced both engines
    # (a.calls == 6); with it, only the first worker did.
    assert slow_loser.calls == 1
    assert winner.calls == 6


async def test_new_job_tries_the_last_winning_engine_before_racing():
    a = Recorder("a", result="from-a", delay=0.05)
    b = Recorder("b", result="from-b")
    reg = make_registry(a, b)
    settings = FakeSettings(("a", "b"))

    first = TranslationManager(engine="auto", settings=settings, registry=reg)
    assert await first.translate("hi", "auto", "fr") == "from-b"  # b wins the race
    a_calls_after_first = a.calls

    second = TranslationManager(engine="auto", settings=settings, registry=reg)
    assert await second.translate("hi again", "auto", "fr") == "from-b"
    assert a.calls == a_calls_after_first  # no new race: preferred engine used directly


async def test_dead_preferred_engine_is_forgotten_and_race_resumes():
    b = Recorder("b", result="from-b")
    a = Recorder("a", result="from-a", delay=0.02)
    reg = make_registry(a, b)
    settings = FakeSettings(("a", "b"))
    await TranslationManager(engine="auto", settings=settings, registry=reg).translate("x", "auto", "fr")
    assert reg.preferred_engine == "b"

    b.fail = TransientTranslationError("now down")
    mgr = TranslationManager(engine="auto", settings=settings, registry=reg)
    assert await mgr.translate("y", "auto", "fr") == "from-a"
    assert reg.preferred_engine == "a"


# -- 6. quota cooldown --------------------------------------------------
def test_session_disable_with_ttl_expires_by_itself():
    health.mark_session_disabled("googletrans", "429", ttl=0.05)
    assert health.is_session_disabled("googletrans")
    time.sleep(0.08)
    assert not health.is_session_disabled("googletrans")
    assert health.disabled_reason("googletrans") is None


def test_session_disable_without_ttl_is_still_permanent_until_health_check():
    health.mark_session_disabled("x", "dead")
    time.sleep(0.05)
    assert health.is_session_disabled("x")
    health.mark_session_enabled("x")
    assert not health.is_session_disabled("x")


async def test_a_quota_failure_in_a_job_disables_with_a_cooldown_not_forever():
    a = Recorder("a", fail=TransientTranslationError("429 Too Many Requests"))
    reg = make_registry(a)
    mgr = TranslationManager(engine="a", settings=FakeSettings(("a",)), registry=reg)
    with pytest.raises(Exception):
        await mgr.translate("hello", "auto", "fr")
    assert health.is_session_disabled("a")
    assert health._disabled_until.get("a") is not None  # has an expiry


# -- 7. persisted settings -----------------------------------------------
def test_stale_settings_file_keeps_admin_choice_but_not_frozen_slow_limits(tmp_path):
    path = tmp_path / "translation_settings.json"
    path.write_text(
        json.dumps(
            {
                "default_engine": "deep-google",
                "requests_per_minute": 50,
                "max_concurrency": 3,
                "provider_requests_per_minute": {},
                "provider_concurrency": {},
            }
        )
    )
    s = TranslationSettings(path)
    assert s.default_engine == "deep-google"  # the admin's choice survives
    assert s.requests_per_minute != 50  # new default, not the frozen old one
    assert s.provider_requests_per_minute["google"] >= 100
    assert s.provider_concurrency["google"] >= 4


def test_current_schema_settings_file_is_honoured_in_full(tmp_path):
    path = tmp_path / "translation_settings.json"
    path.write_text(
        json.dumps({"schema_version": SCHEMA_VERSION, "requests_per_minute": 17, "default_engine": "lingva"})
    )
    s = TranslationSettings(path)
    assert s.requests_per_minute == 17
    assert s.default_engine == "lingva"


# -- 8. found in the second review pass ---------------------------------
@pytest.mark.parametrize(
    "page,expected",
    [
        ("That's an error. There was an error. Please try again later.\n\nReal text", "Real text"),
        ("Error 500 (Server Error)!!1\n1. Chapter one begins", "1. Chapter one begins"),
        ("500. That's an error.\nThere was an error. Please try again later.\nThat's all we know.\nReal text", "Real text"),
        ("500. That\u2019s an error.\r\nReal text", "Real text"),
    ],
)
def test_error_pages_are_stripped_whatever_follows_them(page, expected):
    assert filter_error_text(page) == expected


def test_prose_containing_error_page_phrases_mid_line_is_never_cut():
    line = "He sighed. Please try again later, she said, and left.\nNext line"
    assert filter_error_text(line) == line


def test_language_map_returns_the_engines_own_casing():
    # deep-translator validates codes case-sensitively: "zh-cn" != "zh-CN".
    from deep_translator import MyMemoryTranslator
    from deep_translator.constants import MY_MEMORY_LANGUAGES_TO_CODES as M
    from translation.providers import language_map

    language_map.clear_cache()
    for detected in ("zh-cn", "zh-tw", "ko", "ja", "vi", "he", "ta"):
        mapped = language_map.map_language_code("casing-probe", detected, lambda: M)
        MyMemoryTranslator(source=mapped, target="en-GB")  # raises if unsupported
    assert language_map.map_language_code("casing-probe", "zh-cn", lambda: M) == "zh-CN"


def test_detection_fallback_client_uses_an_httpx_timeout():
    # asyncio.Timeout(10.0) here made every request raise TypeError, so the
    # network fallback detector never worked.
    import httpx

    from translation import detection

    async def build():
        return await detection._get_googletrans_client()

    client = asyncio.run(build())
    assert isinstance(client.client.timeout, httpx.Timeout)


def test_translates_does_not_crash_when_progress_entry_is_missing():
    # The old except-handler re-read bot.translator[user] and raised KeyError.
    from utils.translate import Translator

    bot = MagicMock()
    bot.translator = {}  # entry cleared (e.g. by a restart/cleanup) mid-job
    tr = Translator(bot, user=1, language="english", engine="auto")

    async def fake(chapter):
        return [str(c).upper() for c in chapter]

    tr._atranslate_batch_with_retry = fake
    tr.translates(["a", "b", "c"], 1)
    assert [tr.order[i][0] for i in range(3)] == ["A", "B", "C"]


# -- 9. engine check: batch probe, validation, readable errors ------------
class _ProbeBackend(TranslationBackend):
    def __init__(self, name, single="hola", batch=None, exc=None, batch_exc=None):
        self.name = name
        self.display_name = name
        self.capabilities = ProviderCapabilities()
        self._single, self._batch, self._exc, self._batch_exc = single, batch, exc, batch_exc
        self.batch_calls = 0

    def is_available(self):
        return True

    async def translate(self, text, source_language, target_language):
        if self._exc:
            raise self._exc
        return self._single

    async def translate_batch(self, texts, source_language, target_language):
        self.batch_calls += 1
        if self._batch_exc:
            raise self._batch_exc
        return self._batch if self._batch is not None else ["Hello", "Thank you", "Good morning"]


def _check(*backends):
    return {r.engine: r for r in asyncio.run(health.run_health_check(make_registry(*backends)))}


def test_healthy_engine_reports_single_and_batch_with_timings_and_samples():
    r = _check(_ProbeBackend("good"))["good"]
    assert r.ok and r.single_ok and r.batch_ok
    assert r.single_sample == "hola"
    assert r.batch_sample == "Hello | Thank you | Good morning"
    assert r.single_ms is not None and r.batch_ms is not None


def test_engine_returning_the_string_none_is_no_longer_reported_healthy():
    # translators-mymemory looked "working" in .enginecheck while returning "None".
    r = _check(_ProbeBackend("none", single="None"))["none"]
    assert not r.ok and "None" in r.reason
    assert health.is_session_disabled("none")


def test_engine_returning_a_provider_error_message_is_unhealthy():
    r = _check(_ProbeBackend("mm", single=MYMEMORY_SOFT_ERROR))["mm"]
    assert not r.ok and "rejected the request" in r.reason


def test_batch_failure_is_reported_separately_and_disables_the_engine():
    r = _check(_ProbeBackend("b", batch_exc=TransientTranslationError("ko unsupported")))["b"]
    assert r.single_ok is True and r.batch_ok is False and not r.ok
    assert r.reason == "batch: ko unsupported" and r.batch_reason == "ko unsupported"
    assert health.is_session_disabled("b")


def test_batch_returning_the_wrong_number_of_items_is_caught():
    r = _check(_ProbeBackend("short", batch=["Hello"]))["short"]
    assert r.batch_ok is False and "1 item" in r.reason


def test_batch_probe_is_skipped_when_the_single_probe_already_failed():
    b = _ProbeBackend("dead", exc=TransientTranslationError("down"))
    r = _check(b)["dead"]
    assert r.single_ok is False and r.batch_ok is None
    assert b.batch_calls == 0


def test_timeout_has_a_readable_reason_not_an_empty_string():
    r = _check(_ProbeBackend("slow", exc=asyncio.TimeoutError()))["slow"]
    assert r.reason and "timed out" in r.reason


def test_describe_exception_is_never_empty():
    import httpx

    from translation.errors import describe_exception

    assert describe_exception(asyncio.TimeoutError()) == "TimeoutError"
    assert describe_exception(httpx.ConnectTimeout("")) == "ConnectTimeout"
    assert describe_exception(ValueError("bad")) == "ValueError: bad"
    assert describe_exception(TransientTranslationError("lingva returned HTTP 500")) == "lingva returned HTTP 500"


def test_enginecheck_embed_text_shows_timings_samples_and_batch_failures():
    from cogs.translation import Translate

    ok = health.EngineCheckResult("a", "Good", True, True, False, None, True, 640, "hola", True, 1200, "Hello | Thank you")
    text = Translate._engine_check_detail(ok)
    assert "working" in text and "640ms" in text and "1.2s" in text and "hola" in text and "Hello" in text
    half = health.EngineCheckResult("b", "Half", True, False, False, "batch: HTTP 500", True, 300, "hola", False, None, None, "HTTP 500")
    text = Translate._engine_check_detail(half)
    assert "single ✓" in text and "batch ✗" in text and "HTTP 500" in text


# -- 10. shared rate-limit pools -------------------------------------------
def test_google_family_engines_share_one_rate_group():
    from translation.config import rate_group

    assert rate_group("googletrans") == rate_group("deep-google") == rate_group("translators-google") == "google"
    assert rate_group("deep-mymemory") == rate_group("translators-mymemory") == "mymemory"
    assert rate_group("lingva") == "lingva"


def test_same_upstream_engines_share_a_semaphore_and_bucket_but_others_do_not():
    mgr = TranslationManager(engine="googletrans")
    assert mgr._semaphore_for("googletrans") is mgr._semaphore_for("deep-google")
    assert mgr._semaphore_for("googletrans") is mgr._semaphore_for("translators-google")
    assert mgr._semaphore_for("googletrans") is not mgr._semaphore_for("lingva")
    assert mgr._rate_limit_for("googletrans") is mgr._rate_limit_for("deep-google")
    assert mgr._rate_limit_for("lingva") is not mgr._rate_limit_for("googletrans")


# -- 11. mirror failover -----------------------------------------------------
def test_split_urls_handles_lists_blanks_and_default():
    from translation.providers.http_backend import split_urls

    assert split_urls("https://a.example/, https://b.example ,,", "https://d.example") == [
        "https://a.example",
        "https://b.example",
    ]
    assert split_urls("", "https://d.example/") == ["https://d.example"]
    assert split_urls(None, "https://d.example") == ["https://d.example"]


def test_lingva_fails_over_to_the_next_instance(monkeypatch):
    from translation.providers.lingva_backend import LingvaBackend

    monkeypatch.setenv("LINGVA_URL", "https://dead.example, https://alive.example")
    backend = LingvaBackend()
    seen = []

    async def fake_request(method, url, **kw):
        seen.append(url)
        if "dead.example" in url:
            raise TransientTranslationError("lingva returned HTTP 500")
        return {"translation": "Hello"}

    monkeypatch.setattr(backend, "_request_json", fake_request)
    assert asyncio.run(backend.translate("안녕", "ko", "en")) == "Hello"
    assert "dead.example" in seen[0] and "alive.example" in seen[1]


def test_lingva_raises_the_last_error_when_every_instance_is_down(monkeypatch):
    from translation.providers.lingva_backend import LingvaBackend

    monkeypatch.setenv("LINGVA_URL", "https://a.example,https://b.example")
    backend = LingvaBackend()

    async def always_500(method, url, **kw):
        raise TransientTranslationError("lingva returned HTTP 500")

    monkeypatch.setattr(backend, "_request_json", always_500)
    with pytest.raises(TransientTranslationError, match="HTTP 500"):
        asyncio.run(backend.translate("안녕", "ko", "en"))

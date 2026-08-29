import asyncio

import pytest

from translation.base import TranslationBackend
from translation.errors import (
    AllEnginesFailedError,
    InvalidEngineError,
    TranslationFailedError,
    TransientTranslationError,
)
from translation.manager import TranslationManager
from translation.registry import ProviderRegistry


class FakeSettings:
    """Minimal stand-in for translation.config.TranslationSettings --
    exposes exactly the attributes TranslationManager reads, with values
    the test controls directly instead of going through a JSON file."""

    def __init__(
        self,
        default_engine="a",
        auto_engine_order=None,
        retry_delays=None,
        max_concurrency=5,
        provider_concurrency=None,
    ):
        self.default_engine = default_engine
        self._auto_engine_order = auto_engine_order if auto_engine_order is not None else ["a", "b", "c"]
        self._retry_delays = retry_delays if retry_delays is not None else []
        self.max_concurrency = max_concurrency
        self._provider_concurrency = provider_concurrency or {}

    @property
    def auto_engine_order(self):
        return list(self._auto_engine_order)

    @property
    def retry_delays(self):
        return list(self._retry_delays)

    @property
    def provider_concurrency(self):
        return dict(self._provider_concurrency)


class ScriptedBackend(TranslationBackend):
    """Returns/raises the next item in `script` on each call; the last
    item repeats forever once the script is exhausted."""

    def __init__(self, name, script, display_name=None):
        self.name = name
        self.display_name = display_name or name
        self._script = list(script)
        self.calls = 0

    def is_available(self):
        return True

    async def translate(self, text, source_language, target_language):
        self.calls += 1
        idx = min(self.calls - 1, len(self._script) - 1)
        outcome = self._script[idx]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def make_registry(**backends):
    reg = ProviderRegistry()
    for name, backend in backends.items():
        reg.register(name, lambda b=backend: b, display_name=backend.display_name)
    return reg


# -- explicit engine --------------------------------------------------
async def test_explicit_engine_success():
    a = ScriptedBackend("a", ["bonjour"])
    reg = make_registry(a=a)
    mgr = TranslationManager(engine="a", settings=FakeSettings(), registry=reg)
    assert await mgr.translate("hi", "auto", "fr") == "bonjour"
    assert a.calls == 1


async def test_explicit_engine_fails_without_falling_back():
    a = ScriptedBackend("a", [TransientTranslationError("down")])
    b = ScriptedBackend("b", ["should never be used"])
    reg = make_registry(a=a, b=b)
    mgr = TranslationManager(engine="a", settings=FakeSettings(retry_delays=[]), registry=reg)
    with pytest.raises(TranslationFailedError):
        await mgr.translate("hi", "auto", "fr")
    assert b.calls == 0  # never touched -- explicit choice must be respected


async def test_explicit_engine_retries_before_giving_up():
    a = ScriptedBackend(
        "a", [TransientTranslationError("x"), TransientTranslationError("x"), "ok"]
    )
    reg = make_registry(a=a)
    mgr = TranslationManager(engine="a", settings=FakeSettings(retry_delays=[0, 0]), registry=reg)
    assert await mgr.translate("hi", "auto", "fr") == "ok"
    assert a.calls == 3


async def test_unavailable_explicit_engine_raises_invalid_engine_error():
    reg = ProviderRegistry()  # nothing registered
    mgr = TranslationManager(engine="ghost-engine", settings=FakeSettings(), registry=reg)
    with pytest.raises(InvalidEngineError):
        await mgr.translate("hi", "auto", "fr")


# -- default mode -------------------------------------------------------
async def test_default_mode_uses_configured_engine():
    a = ScriptedBackend("a", ["translated-a"])
    b = ScriptedBackend("b", ["translated-b"])
    reg = make_registry(a=a, b=b)
    mgr = TranslationManager(engine="default", settings=FakeSettings(default_engine="b"), registry=reg)
    assert mgr.resolved_engine_name() == "b"
    assert await mgr.translate("hi", "auto", "fr") == "translated-b"
    assert a.calls == 0


async def test_default_resolution_is_frozen_at_job_start():
    a = ScriptedBackend("a", ["translated-a"])
    b = ScriptedBackend("b", ["translated-b"])
    reg = make_registry(a=a, b=b)
    settings = FakeSettings(default_engine="a")
    mgr = TranslationManager(engine="default", settings=settings, registry=reg)
    # Admin changes the global default *after* the job already started.
    settings.default_engine = "b"
    assert await mgr.translate("hi", "auto", "fr") == "translated-a"


# -- auto mode ------------------------------------------------------
async def test_auto_stays_on_first_healthy_engine():
    a = ScriptedBackend("a", ["ok"])
    reg = make_registry(a=a)
    mgr = TranslationManager(
        engine="auto", settings=FakeSettings(auto_engine_order=["a"], retry_delays=[]), registry=reg
    )
    await mgr.translate("1", "auto", "fr")
    await mgr.translate("2", "auto", "fr")
    assert a.calls == 2
    assert mgr.state.active_engine == "a"


async def test_auto_falls_back_when_first_engine_fails():
    a = ScriptedBackend("a", [TransientTranslationError("down")])
    b = ScriptedBackend("b", ["ok"])
    reg = make_registry(a=a, b=b)
    mgr = TranslationManager(
        engine="auto", settings=FakeSettings(auto_engine_order=["a", "b"], retry_delays=[]), registry=reg
    )
    assert await mgr.translate("hi", "auto", "fr") == "ok"
    assert mgr.state.active_engine == "b"
    assert a.calls == 1
    assert b.calls == 1


async def test_auto_falls_back_through_three_engines():
    a = ScriptedBackend("a", [TransientTranslationError("down")])
    b = ScriptedBackend("b", [TransientTranslationError("down")])
    c = ScriptedBackend("c", ["ok"])
    reg = make_registry(a=a, b=b, c=c)
    mgr = TranslationManager(
        engine="auto",
        settings=FakeSettings(auto_engine_order=["a", "b", "c"], retry_delays=[]),
        registry=reg,
    )
    assert await mgr.translate("hi", "auto", "fr") == "ok"
    assert mgr.state.active_engine == "c"


async def test_auto_reuses_successful_engine_on_next_chunk_without_reprobing():
    a = ScriptedBackend("a", [TransientTranslationError("down")])
    b = ScriptedBackend("b", ["chunk1", "chunk2"])
    reg = make_registry(a=a, b=b)
    mgr = TranslationManager(
        engine="auto", settings=FakeSettings(auto_engine_order=["a", "b"], retry_delays=[]), registry=reg
    )
    assert await mgr.translate("1", "auto", "fr") == "chunk1"
    assert await mgr.translate("2", "auto", "fr") == "chunk2"
    # `a` must only have been probed once -- not once per chunk.
    assert a.calls == 1
    assert b.calls == 2


async def test_failed_engine_is_skipped_after_active_engine_also_fails():
    # a always fails. b succeeds once, then fails. c always succeeds.
    a = ScriptedBackend("a", [TransientTranslationError("down")])
    b = ScriptedBackend("b", ["ok-once", TransientTranslationError("down-now")])
    c = ScriptedBackend("c", ["ok"])
    reg = make_registry(a=a, b=b, c=c)
    mgr = TranslationManager(
        engine="auto",
        settings=FakeSettings(auto_engine_order=["a", "b", "c"], retry_delays=[]),
        registry=reg,
    )
    assert await mgr.translate("1", "auto", "fr") == "ok-once"
    assert mgr.state.active_engine == "b"

    assert await mgr.translate("2", "auto", "fr") == "ok"
    assert mgr.state.active_engine == "c"
    # `a` was never retried on the second chunk -- it stayed in
    # failed_engines from the very first call.
    assert a.calls == 1
    assert b.calls == 2
    assert c.calls == 1


async def test_per_job_failed_state_does_not_leak_between_jobs():
    # Shared backend instance/registry (as in production, providers are
    # process-lifetime singletons). Job A exhausts both of its attempts
    # (initial pass + the one bounded reset pass) against a permanently
    # failing engine; job B is a fresh manager and must still be willing
    # to try that same engine from scratch.
    a = ScriptedBackend(
        "a", [TransientTranslationError("down1"), TransientTranslationError("down2"), "recovered"]
    )
    reg = make_registry(a=a)
    settings = FakeSettings(auto_engine_order=["a"], retry_delays=[])

    job_a = TranslationManager(engine="auto", settings=settings, registry=reg)
    with pytest.raises(AllEnginesFailedError):
        await job_a.translate("x", "auto", "fr")
    assert "a" in job_a.state.failed_engines

    job_b = TranslationManager(engine="auto", settings=settings, registry=reg)
    assert job_b.state.failed_engines == set()  # fresh state, unaffected by job_a
    assert await job_b.translate("x", "auto", "fr") == "recovered"


async def test_all_engines_fail_bounded_reset_then_raises():
    a = ScriptedBackend("a", [TransientTranslationError("boom")])
    b = ScriptedBackend("b", [TransientTranslationError("boom")])
    reg = make_registry(a=a, b=b)
    mgr = TranslationManager(
        engine="auto", settings=FakeSettings(auto_engine_order=["a", "b"], retry_delays=[]), registry=reg
    )
    with pytest.raises(AllEnginesFailedError):
        await mgr.translate("hi", "auto", "fr")
    # Bounded: exactly one reset pass, so each engine is tried at most
    # twice total (initial pass + one reset pass) -- never unbounded.
    assert a.calls == 2
    assert b.calls == 2


# -- concurrency ------------------------------------------------------
async def test_concurrency_is_bounded_per_provider():
    max_concurrent = 0
    current = 0
    state_lock = asyncio.Lock()

    class SlowBackend(TranslationBackend):
        name = "slow"
        display_name = "slow"

        def is_available(self):
            return True

        async def translate(self, text, source_language, target_language):
            nonlocal max_concurrent, current
            async with state_lock:
                current += 1
                max_concurrent = max(max_concurrent, current)
            await asyncio.sleep(0.05)
            async with state_lock:
                current -= 1
            return "ok"

    reg = ProviderRegistry()
    reg.register("slow", SlowBackend)
    mgr = TranslationManager(
        engine="default",
        settings=FakeSettings(default_engine="slow", max_concurrency=2),
        registry=reg,
    )
    await asyncio.gather(*[mgr.translate(str(i), "auto", "fr") for i in range(6)])
    assert max_concurrent <= 2

import asyncio
import time

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
        requests_per_minute=1_000_000,
        provider_requests_per_minute=None,
    ):
        self.default_engine = default_engine
        self._auto_engine_order = auto_engine_order if auto_engine_order is not None else ["a", "b", "c"]
        self._retry_delays = retry_delays if retry_delays is not None else []
        self.max_concurrency = max_concurrency
        self._provider_concurrency = provider_concurrency or {}
        # High enough that the rate limiter never throttles a test unless
        # a test explicitly wants to exercise it.
        self.requests_per_minute = requests_per_minute
        self._provider_requests_per_minute = provider_requests_per_minute or {}

    @property
    def auto_engine_order(self):
        return list(self._auto_engine_order)

    @property
    def retry_delays(self):
        return list(self._retry_delays)

    @property
    def provider_concurrency(self):
        return dict(self._provider_concurrency)

    @property
    def provider_requests_per_minute(self):
        return dict(self._provider_requests_per_minute)


class ScriptedBackend(TranslationBackend):
    """Returns/raises the next item in `script` on each call; the last
    item repeats forever once the script is exhausted.

    `delay` (seconds) lets a test make race outcomes deterministic: give
    the engine that should "lose" a race a small delay so the instant
    engine wins every time instead of the outcome depending on asyncio
    scheduling order. `calls` counts every invocation (including ones
    later cancelled mid-race -- a cancelled request was still genuinely
    fired); `completed` only counts ones that ran to completion.
    """

    def __init__(self, name, script, display_name=None, delay=0.0):
        self.name = name
        self.display_name = display_name or name
        self._script = list(script)
        self.calls = 0
        self.completed = 0
        self.delay = delay

    def is_available(self):
        return True

    async def translate(self, text, source_language, target_language):
        self.calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        idx = min(self.calls - 1, len(self._script) - 1)
        outcome = self._script[idx]
        self.completed += 1
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
    # a always fails instantly. b succeeds once, then fails. c would also
    # succeed, but has a small delay -- since Auto now *races* every
    # candidate concurrently instead of trying them one at a time (see
    # TranslationManager._discover_auto_engine), giving c a delay makes
    # the race outcome deterministic (b, being instant, always wins over
    # c) instead of depending on asyncio scheduling order.
    a = ScriptedBackend("a", [TransientTranslationError("down")])
    b = ScriptedBackend("b", ["ok-once", TransientTranslationError("down-now")])
    c = ScriptedBackend("c", ["ok"], delay=0.05)
    reg = make_registry(a=a, b=b, c=c)
    mgr = TranslationManager(
        engine="auto",
        settings=FakeSettings(auto_engine_order=["a", "b", "c"], retry_delays=[]),
        registry=reg,
    )
    assert await mgr.translate("1", "auto", "fr") == "ok-once"
    assert mgr.state.active_engine == "b"
    # c was raced alongside b and lost -- it was genuinely invoked (a
    # request was fired) but cancelled before it could complete.
    assert c.calls == 1
    assert c.completed == 0

    assert await mgr.translate("2", "auto", "fr") == "ok"
    assert mgr.state.active_engine == "c"
    # `a` was never retried on the second chunk -- it stayed in
    # failed_engines from the very first call. `b` failed on reuse and
    # was retried directly (not raced, since it was the proven-healthy
    # active engine) before Auto moved on.
    assert a.calls == 1
    assert b.calls == 2
    assert c.calls == 2
    assert c.completed == 1


async def test_auto_races_candidates_and_a_faster_later_engine_can_win():
    # The literal feature this exercises: Auto no longer waits on
    # engines strictly in auto_engine_order -- it fires all currently
    # available candidates at once and adopts whichever responds first.
    # Here "a" is first in the order but slow; "b" is second but fast.
    # The old sequential design would always try "a" to completion
    # before ever touching "b"; the race should pick "b".
    a = ScriptedBackend("a", ["from-a"], delay=0.1)
    b = ScriptedBackend("b", ["from-b"], delay=0.0)
    reg = make_registry(a=a, b=b)
    mgr = TranslationManager(
        engine="auto", settings=FakeSettings(auto_engine_order=["a", "b"], retry_delays=[]), registry=reg
    )
    result = await mgr.translate("hi", "auto", "fr")
    assert result == "from-b"
    assert mgr.state.active_engine == "b"
    # "a" was still fired (that's the "try all" part) but never got to
    # finish -- it lost the race and was cancelled mid-flight.
    assert a.calls == 1
    assert a.completed == 0
    assert b.completed == 1


async def test_auto_race_survives_a_loser_raising_after_being_cancelled():
    # Regression-shaped test: make sure a raced-but-cancelled engine
    # doesn't leave an "exception was never retrieved" warning or crash
    # the winning result. The loser here fails (not just runs slow), so
    # its task completes with an exception concurrently with the winner.
    a = ScriptedBackend("a", [TransientTranslationError("slow failure")], delay=0.05)
    b = ScriptedBackend("b", ["fast success"], delay=0.0)
    reg = make_registry(a=a, b=b)
    mgr = TranslationManager(
        engine="auto", settings=FakeSettings(auto_engine_order=["a", "b"], retry_delays=[]), registry=reg
    )
    assert await mgr.translate("hi", "auto", "fr") == "fast success"
    assert mgr.state.active_engine == "b"


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


# -- rate limiting ------------------------------------------------
async def test_requests_per_minute_is_enforced_per_provider(monkeypatch):
    from translation.ratelimit import TokenBucket

    a = ScriptedBackend("rl-engine", ["ok"] * 10)
    reg = make_registry(**{"rl-engine": a})
    mgr = TranslationManager(engine="default", settings=FakeSettings(default_engine="rl-engine"), registry=reg)

    # Swap in a bucket with a fast (sub-second) refill so the test
    # doesn't have to wait on real per-minute timing to prove the
    # throttling wiring works -- the token-bucket math itself (burst,
    # refill rate, blocking acquire) is already covered thoroughly and
    # quickly in tests/test_translation_ratelimit.py.
    fast_bucket = TokenBucket(rate=2, per_seconds=0.2, burst=1)
    monkeypatch.setattr(mgr, "_rate_limit_for", lambda engine_name: fast_bucket)

    start = time.monotonic()
    await mgr.translate("1", "auto", "fr")
    await mgr.translate("2", "auto", "fr")
    elapsed = time.monotonic() - start
    # burst=1 means the second call must wait for a refill (~0.1s at
    # 2 tokens/0.2s) -- generous bounds to avoid CI timing flakiness.
    assert elapsed >= 0.05


async def test_rate_limit_does_not_apply_across_unrelated_providers():
    a = ScriptedBackend("rl-a", ["ok"] * 5)
    b = ScriptedBackend("rl-b", ["ok"] * 5)
    reg = make_registry(**{"rl-a": a, "rl-b": b})
    settings = FakeSettings(provider_requests_per_minute={"rl-a": 1})  # extremely tight
    mgr_a = TranslationManager(engine="rl-a", settings=settings, registry=reg)
    mgr_b = TranslationManager(engine="rl-b", settings=settings, registry=reg)

    start = time.monotonic()
    await mgr_b.translate("1", "auto", "fr")
    await mgr_b.translate("2", "auto", "fr")
    elapsed = time.monotonic() - start
    # "rl-b" has no tight limit configured (falls back to FakeSettings'
    # very high default) -- it must not be throttled by "rl-a"'s limit.
    assert elapsed < 0.5
    # Sanity: mgr_a is still constructed correctly against the tight limit.
    assert mgr_a.resolved_engine_name() == "rl-a"


# -- concurrency ------------------------------------------------------
async def test_concurrency_is_bounded_per_provider():
    max_concurrent = 0
    current = 0
    state_lock = asyncio.Lock()

    class SlowBackend(TranslationBackend):
        name = "slow-concurrency-test"
        display_name = "slow-concurrency-test"

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
    reg.register("slow-concurrency-test", SlowBackend)
    mgr = TranslationManager(
        engine="default",
        settings=FakeSettings(default_engine="slow-concurrency-test", max_concurrency=2),
        registry=reg,
    )
    await asyncio.gather(*[mgr.translate(str(i), "auto", "fr") for i in range(6)])
    assert max_concurrent <= 2

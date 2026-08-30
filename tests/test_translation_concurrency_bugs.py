import asyncio
import threading

from translation.base import TranslationBackend
from translation.manager import TranslationManager
from translation.registry import ProviderRegistry

# TranslationManager's per-provider semaphore/rate-limiter caches are
# process-wide, keyed only by engine name (see manager.py/ratelimit.py) --
# intentionally, so they bound load across every job in the process, not
# just one. That means engine names must be unique *per test*, not just
# per test file: reusing a name a previous test already touched would
# reuse its cached semaphore/bucket state too. "slow" was previously
# reused across two different test files and, when one of them failed
# without releasing a manually-held permit, permanently hung the other.
_ENGINE_CANCEL_TEST = "slow-cancel-leak-test"


def run(coro):
    return asyncio.run(coro)


class _SlowBackend(TranslationBackend):
    name = _ENGINE_CANCEL_TEST
    display_name = "slow"

    def is_available(self):
        return True

    async def translate(self, text, source_language, target_language):
        await asyncio.sleep(0.05)
        return "ok"


class FakeSettings:
    def __init__(self, max_concurrency=1):
        self.default_engine = _ENGINE_CANCEL_TEST
        self.max_concurrency = max_concurrency
        # High enough to never actually throttle this test.
        self.requests_per_minute = 1_000_000

    @property
    def auto_engine_order(self):
        return [_ENGINE_CANCEL_TEST]

    @property
    def retry_delays(self):
        return []

    @property
    def provider_concurrency(self):
        return {}

    @property
    def provider_requests_per_minute(self):
        return {}


def test_cancelling_a_caller_waiting_on_a_saturated_semaphore_does_not_leak_the_permit():
    # Regression test for a real bug found in review: the per-provider
    # concurrency semaphore was acquired via a *bare*
    # `await asyncio.to_thread(sem.acquire)`. The background thread
    # doing that acquire cannot be interrupted, so if the *awaiting*
    # coroutine got cancelled before the acquire completed, nobody was
    # left to release the permit it eventually got -- permanently
    # shrinking that provider's effective concurrency limit. This test
    # saturates a max_concurrency=1 semaphore, cancels the second waiter
    # while it's still blocked waiting for a permit, and asserts the
    # semaphore is still fully usable afterwards (i.e. the permit that
    # cancelled waiter eventually acquired in the background was given
    # back, not lost).
    reg = ProviderRegistry()
    reg.register(_ENGINE_CANCEL_TEST, _SlowBackend)
    mgr = TranslationManager(engine="default", settings=FakeSettings(max_concurrency=1), registry=reg)

    async def scenario():
        # Take the single permit and hold it while we set up the
        # cancellation scenario. try/finally below guarantees this gets
        # released even if an assertion fails partway through -- this
        # engine name's semaphore is a process-wide singleton (see the
        # module docstring above), so leaving it exhausted here would
        # silently hang every *other* test that happened to reuse the
        # name, not just fail this one.
        sem = mgr._semaphore_for(_ENGINE_CANCEL_TEST)
        held = await asyncio.to_thread(sem.acquire)
        assert held
        released = False
        try:
            async def call():
                return await mgr._call_backend(
                    _ENGINE_CANCEL_TEST, lambda backend: backend.translate("x", "auto", "fr")
                )

            waiter = asyncio.ensure_future(call())
            # Give the waiter a moment to actually start blocking on the
            # semaphore (its background acquire-thread is now running).
            await asyncio.sleep(0.05)
            waiter.cancel()
            try:
                await waiter
            except asyncio.CancelledError:
                pass

            # Release the permit we were holding, then give the waiter's
            # background acquire-thread a moment to pick it up and (per
            # the fix) hand it right back since nobody's using it
            # anymore.
            sem.release()
            released = True
            await asyncio.sleep(0.2)

            # If the permit leaked, this would time out forever (the
            # semaphore would already be "full" with nobody able to
            # release). Bound it so a regression fails the test instead
            # of hanging the suite.
            acquired = await asyncio.wait_for(asyncio.to_thread(sem.acquire), timeout=1.0)
            assert acquired
            sem.release()
        finally:
            if not released:
                sem.release()

    run(scenario())


def test_cross_thread_client_construction_does_not_duplicate_or_deadlock():
    # Regression test for a real bug found in review: provider backends
    # are process-wide singletons (see translation/registry.py) but were
    # guarding their lazily-created client with an *asyncio.Lock*, even
    # though this project calls into them from many different OS
    # threads, each running its own independent event loop
    # (utils/translate.py's Translator.translates() uses a
    # ThreadPoolExecutor where each worker does its own asyncio.run()
    # per chunk). asyncio.Lock has no cross-thread guarantees, so two
    # threads racing through "is there a client yet?" could both
    # construct one, silently leaking the loser. The fix switched these
    # guards to threading.Lock. This drives real concurrent construction
    # from multiple OS threads and asserts exactly one client survives
    # per cache key, with no deadlock/exception.
    from translation.providers.deep_translator_backend import _DeepTranslatorBackend

    build_calls = []
    build_lock = threading.Lock()

    class _CountingClient:
        def __init__(self, source, target):
            with build_lock:
                build_calls.append((source, target))
            self.source = source
            self.target = target

    class _CountingBackend(_DeepTranslatorBackend):
        name = "counting"
        display_name = "counting"
        _client_cls = _CountingClient

        def is_available(self):
            return True

    backend = _CountingBackend()
    results = []
    errors = []

    def worker():
        try:
            client = backend._get_client("auto", "fr")
            results.append(client)
        except Exception as exc:  # pragma: no cover - failure path
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(16)]
    for th in threads:
        th.start()
    for th in threads:
        th.join(timeout=5)

    assert not errors
    assert len(results) == 16
    # Exactly one underlying client should have been built for this
    # (source, target) key, and every caller should have gotten it.
    assert len(build_calls) == 1
    assert len(set(id(r) for r in results)) == 1

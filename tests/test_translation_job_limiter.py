import asyncio

from translation.config import TranslationSettings
from translation.jobs import JobSlotLimiter


def run(coro):
    return asyncio.run(coro)


def test_allows_up_to_the_configured_number_of_concurrent_jobs(tmp_path):
    settings = TranslationSettings(path=tmp_path / "settings.json")
    settings._data["max_concurrent_jobs"] = 2
    limiter = JobSlotLimiter(settings=settings)

    async def scenario():
        current = 0
        max_seen = 0
        lock = asyncio.Lock()

        async def job():
            nonlocal current, max_seen
            async with await limiter.acquire():
                async with lock:
                    current += 1
                    max_seen = max(max_seen, current)
                await asyncio.sleep(0.05)
                async with lock:
                    current -= 1

        await asyncio.gather(*[job() for _ in range(6)])
        return max_seen

    max_seen = run(scenario())
    assert max_seen <= 2


def test_a_job_beyond_the_cap_waits_rather_than_running_immediately(tmp_path):
    settings = TranslationSettings(path=tmp_path / "settings.json")
    settings._data["max_concurrent_jobs"] = 1
    limiter = JobSlotLimiter(settings=settings)

    async def scenario():
        events = []

        async def job(name, hold):
            async with await limiter.acquire():
                events.append(f"{name}-start")
                await asyncio.sleep(hold)
                events.append(f"{name}-end")

        await asyncio.gather(job("first", 0.1), job("second", 0.0))
        return events

    events = run(scenario())
    # With only one slot, "second" must not start until "first" finished
    # -- i.e. it queues instead of running alongside it.
    assert events == ["first-start", "first-end", "second-start", "second-end"]

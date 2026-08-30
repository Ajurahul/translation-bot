import time

from translation.ratelimit import RateLimiterRegistry, TokenBucket


def test_allows_burst_up_to_capacity_immediately():
    bucket = TokenBucket(rate=5, per_seconds=60.0)
    start = time.monotonic()
    for _ in range(5):
        assert bucket.acquire(timeout=0) is True
    elapsed = time.monotonic() - start
    assert elapsed < 0.2  # all five came from the initial burst, no waiting


def test_blocks_once_capacity_is_exhausted():
    bucket = TokenBucket(rate=5, per_seconds=60.0)
    for _ in range(5):
        assert bucket.acquire(timeout=0) is True
    # The bucket is empty now -- a zero-timeout acquire must fail rather
    # than silently letting an extra request through.
    assert bucket.acquire(timeout=0) is False


def test_refills_over_time():
    # 60 tokens/minute == 1 token/second.
    bucket = TokenBucket(rate=60, per_seconds=60.0, burst=1)
    assert bucket.acquire(timeout=0) is True
    assert bucket.acquire(timeout=0) is False
    time.sleep(1.1)
    assert bucket.acquire(timeout=0) is True


def test_acquire_blocks_until_a_token_is_available():
    bucket = TokenBucket(rate=60, per_seconds=60.0, burst=1)
    assert bucket.acquire(timeout=0) is True
    start = time.monotonic()
    acquired = bucket.acquire(timeout=2.0)
    elapsed = time.monotonic() - start
    assert acquired is True
    assert 0.5 <= elapsed <= 1.5


def test_registry_returns_same_bucket_for_same_engine_and_rate():
    registry = RateLimiterRegistry()
    a = registry.get("googletrans", 50)
    b = registry.get("googletrans", 50)
    assert a is b


def test_registry_gives_different_engines_independent_buckets():
    registry = RateLimiterRegistry()
    a = registry.get("googletrans", 1)
    b = registry.get("translators-bing", 1)
    assert a is not b
    assert a.acquire(timeout=0) is True
    assert a.acquire(timeout=0) is False
    # Exhausting engine A's bucket must not affect engine B's.
    assert b.acquire(timeout=0) is True

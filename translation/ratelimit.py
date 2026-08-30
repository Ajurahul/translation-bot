"""Requests-per-minute rate limiting, independent of concurrency bounding.

`TranslationManager`'s per-provider semaphore (see manager.py) only bounds
how many calls to a provider can be *in flight* at once -- it does nothing
to stop those calls from firing back-to-back as fast as each one
completes. A provider that returns quickly can still be hammered at a
high requests/second rate even with max_concurrency=1. A token bucket
adds the missing piece: a hard cap on requests *per minute*, which is
what actually avoids provider-side rate-limit bans.
"""
import threading
import time
import typing as t


class TokenBucket:
    """Refills `rate` tokens every `per_seconds`, capped at `burst`
    tokens banked at once. `acquire()` blocks the calling thread until a
    token is available (or `timeout` elapses)."""

    def __init__(self, rate: float, per_seconds: float = 60.0, burst: t.Optional[float] = None) -> None:
        self.rate = max(0.001, float(rate))
        self.per_seconds = max(0.001, float(per_seconds))
        self.capacity = float(burst) if burst is not None else self.rate
        self._tokens = self.capacity
        self._last_refill = time.monotonic()
        self._lock = threading.Lock()

    def _refill_locked(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_refill
        if elapsed <= 0:
            return
        self._tokens = min(self.capacity, self._tokens + elapsed * (self.rate / self.per_seconds))
        self._last_refill = now

    def acquire(self, timeout: t.Optional[float] = None) -> bool:
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            with self._lock:
                self._refill_locked()
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return True
                shortfall = 1.0 - self._tokens
                wait = shortfall * (self.per_seconds / self.rate)
            if deadline is not None and time.monotonic() + wait > deadline:
                return False
            time.sleep(min(wait, 0.5))


class RateLimiterRegistry:
    """Process-wide token bucket per provider id, sized from
    translation.config settings. Mirrors the pattern used for the
    per-provider concurrency semaphores in TranslationManager."""

    def __init__(self) -> None:
        self._buckets: t.Dict[t.Tuple[str, float, float], TokenBucket] = {}
        self._lock = threading.Lock()

    def get(self, engine_name: str, rate_per_minute: float, burst: t.Optional[float] = None) -> TokenBucket:
        burst_value = float(burst) if burst is not None else float(rate_per_minute)
        key = (engine_name, float(rate_per_minute), burst_value)
        with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None:
                bucket = TokenBucket(rate=rate_per_minute, per_seconds=60.0, burst=burst_value)
                self._buckets[key] = bucket
            return bucket


rate_limiters = RateLimiterRegistry()

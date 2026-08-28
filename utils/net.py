"""
Centralized HTTP fetching for the crawler.

Goals (see docs/HEADLESS_CRAWLER_IMPROVEMENTS.md for the browser side):
  - One reusable, connection-pooled requests.Session per crawl lifecycle
    instead of a brand-new session/connection per request.
  - Consistent, bounded retry/backoff for transient network errors.
  - A single, bounded escalation path to cloudscraper for Cloudflare-style
    blocks: try up to MAX_CLOUD_ATTEMPTS times with exponential backoff,
    then give up cleanly (``blocked=True``) and let the caller log/report
    instead of looping forever or crashing.

This module intentionally does NOT add any new evasion technique (no proxy
rotation, no IP rotation, no CAPTCHA solving). It only wraps the
cloudscraper dependency that was already part of this project, bounds how
many times it is retried, and makes the give-up path explicit.
"""
import logging
import random
import threading
import time
from dataclasses import dataclass
from typing import Optional

import cloudscraper
import requests
from requests.adapters import HTTPAdapter

try:
    from urllib3.util.retry import Retry
except ImportError:  # very old urllib3 fallback path
    from requests.packages.urllib3.util.retry import Retry

logger = logging.getLogger("crawler.net")

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/109.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_1) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.1 Safari/605.1.15",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/108.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36",
]

# Substrings that show up on Cloudflare's own interstitial / challenge pages.
# Used only to *detect* the state, never to defeat it beyond what
# cloudscraper (already a project dependency) already does.
CLOUDFLARE_MARKERS = (
    "checking your browser before accessing",
    "cf-browser-verification",
    "cf-chl",
    "attention required! | cloudflare",
    "just a moment...",
    "ddos protection by cloudflare",
    "cloudflare ray id",
)


def random_headers() -> dict:
    return {"User-Agent": random.choice(USER_AGENTS)}


def looks_like_cloudflare_block(status_code: Optional[int], text: str) -> bool:
    """Best-effort detection of a Cloudflare challenge/interstitial page.
    Only used to decide whether to escalate to cloudscraper / how to log
    the eventual failure - never to attempt any additional evasion."""
    if not text:
        return status_code in (403, 503)
    low = text[:4000].lower()
    if any(marker in low for marker in CLOUDFLARE_MARKERS):
        return True
    return status_code in (403, 503) and "cloudflare" in low


@dataclass
class FetchResult:
    ok: bool
    response: Optional[requests.Response] = None
    used_cloudscraper: bool = False
    blocked: bool = False
    error: Optional[str] = None


class CrawlSession:
    """One of these should be created per logical crawler lifecycle (i.e.
    once per /crawl or /crawlnext invocation) and reused for every request
    made during that run, instead of building a new session/scraper per
    request. Thread-safe enough for the existing ThreadPoolExecutor usage
    in cogs/crawler.py (requests.Session is safe to share across threads
    for simple GETs, which is the only thing this project does with it)."""

    MAX_CLOUD_ATTEMPTS = 5

    def __init__(self, allow_cloudscrape: bool = True, timeout: float = 20,
                 max_cloud_attempts: Optional[int] = None):
        self.timeout = timeout
        self.allow_cloudscrape = allow_cloudscrape
        self.max_cloud_attempts = max_cloud_attempts or self.MAX_CLOUD_ATTEMPTS
        self._session = self._build_session()
        self._cloud_session = None
        self._cloud_lock = threading.Lock()

    @staticmethod
    def _build_session() -> requests.Session:
        session = requests.Session()
        retry = Retry(
            total=3,
            connect=3,
            read=3,
            backoff_factor=1.5,
            status_forcelist=(429, 500, 502, 504),
            allowed_methods=frozenset(["GET", "HEAD"]),
            respect_retry_after_header=True,
        )
        adapter = HTTPAdapter(max_retries=retry, pool_connections=10, pool_maxsize=10)
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        return session

    def _get_cloud_session(self, recreate: bool = False) -> cloudscraper.CloudScraper:
        with self._cloud_lock:
            if self._cloud_session is None or recreate:
                if recreate and self._cloud_session is not None:
                    try:
                        self._cloud_session.close()
                    except Exception:
                        pass
                self._cloud_session = cloudscraper.CloudScraper(delay=10)
            return self._cloud_session

    def get_cloud_session(self) -> cloudscraper.CloudScraper:
        """Returns (creating if necessary) the underlying, reusable
        cloudscraper session, for callers that need to pass a raw
        cloudscraper-compatible object around (e.g. getcontent()'s
        per-chapter fetch loop in cogs/crawler.py) rather than going
        through .get(). Reuses the same instance across the whole crawl
        lifecycle instead of creating a fresh one per chapter."""
        return self._get_cloud_session()

    def get(self, url: str, *, headers: Optional[dict] = None,
            timeout: Optional[float] = None, **kwargs) -> FetchResult:
        """Fetch ``url``. Tries the plain pooled session first; if that
        fails outright or the response looks like a Cloudflare challenge,
        escalates to cloudscraper for up to ``max_cloud_attempts`` bounded,
        backed-off attempts. After that it gives up and returns
        ``FetchResult(ok=False, blocked=True)`` instead of retrying
        forever - callers should treat that as "unsupported for now" and
        stop/report, per configuration."""
        timeout = timeout or self.timeout
        headers = headers or random_headers()

        last_error = None
        try:
            resp = self._session.get(url, headers=headers, timeout=timeout, **kwargs)
        except requests.RequestException as e:
            last_error = e
            resp = None
            logger.info("[net] plain request failed for %s: %s", url, e)

        if resp is not None:
            body_preview = resp.text[:4000] if resp.content else ""
            if resp.status_code < 400 and not looks_like_cloudflare_block(resp.status_code, body_preview):
                return FetchResult(ok=True, response=resp, used_cloudscraper=False)
            if not looks_like_cloudflare_block(resp.status_code, body_preview):
                # A genuine, non-Cloudflare error (404, plain 403, etc.) -
                # don't burn cloudscraper attempts on these.
                return FetchResult(ok=False, response=resp, used_cloudscraper=False,
                                   error=f"HTTP {resp.status_code}")
            logger.info("[net] Cloudflare-style block detected for %s (status=%s)", url, resp.status_code)

        if not self.allow_cloudscrape:
            return FetchResult(ok=False, blocked=True,
                               error=str(last_error) if last_error else "request failed, cloudscrape disabled")

        return self._get_via_cloudscraper(url, headers=headers, timeout=timeout, **kwargs)

    def _get_via_cloudscraper(self, url: str, *, headers: dict, timeout: float, **kwargs) -> FetchResult:
        delay = 2.0
        recreate = False
        for attempt in range(1, self.max_cloud_attempts + 1):
            try:
                cloud = self._get_cloud_session(recreate=recreate)
                resp = cloud.get(url, headers=headers, timeout=timeout, **kwargs)
            except Exception as e:
                logger.info("[net] cloudscraper error for %s (attempt %d/%d): %s",
                           url, attempt, self.max_cloud_attempts, e)
                recreate = True
                time.sleep(delay)
                delay = min(delay * 2, 15)
                continue

            recreate = False
            if resp is not None and resp.status_code < 400 and \
               not looks_like_cloudflare_block(resp.status_code, resp.text[:4000] if resp.content else ""):
                logger.info("[net] cloudscraper succeeded for %s on attempt %d/%d",
                           url, attempt, self.max_cloud_attempts)
                return FetchResult(ok=True, response=resp, used_cloudscraper=True)

            logger.info("[net] cloudscraper still blocked for %s (status=%s, attempt %d/%d)",
                       url, getattr(resp, "status_code", "?"), attempt, self.max_cloud_attempts)
            time.sleep(delay)
            delay = min(delay * 2, 15)

        logger.warning(
            "[net] giving up on %s after %d cloudscraper attempts - treating as an unsupported/blocked "
            "access state (Cloudflare or persistent site block)", url, self.max_cloud_attempts)
        return FetchResult(
            ok=False, blocked=True,
            error=f"Site did not become reachable after {self.max_cloud_attempts} cloudscraper attempts "
                  f"(likely an unsolved Cloudflare challenge). Try again later."
        )

    def close(self):
        try:
            self._session.close()
        except Exception:
            pass
        if self._cloud_session is not None:
            try:
                self._cloud_session.close()
            except Exception:
                pass


def fetch_with_retries(url: str, *, scraper: Optional[cloudscraper.CloudScraper] = None,
                       headers: Optional[dict] = None, timeout: float = 20,
                       retries: int = 3, base_delay: float = 2.0) -> Optional[requests.Response]:
    """Small helper for call sites (like the ThreadPoolExecutor workers in
    cogs/crawler.py) that already manage their own shared scraper/session
    object and just need bounded, backed-off retries around a single GET,
    without pulling in a full CrawlSession. Returns None (never raises)
    after exhausting retries so callers keep their existing
    'response is None means failed' handling."""
    headers = headers or random_headers()
    delay = base_delay
    for attempt in range(1, retries + 1):
        try:
            getter = scraper.get if scraper is not None else requests.get
            resp = getter(url, headers=headers, timeout=timeout)
            if resp is not None:
                return resp
        except Exception as e:
            logger.info("[net] fetch_with_retries error for %s (attempt %d/%d): %s", url, attempt, retries, e)
        if attempt < retries:
            time.sleep(delay)
            delay = min(delay * 2, 10)
    logger.info("[net] fetch_with_retries exhausted %d attempts for %s", retries, url)
    return None

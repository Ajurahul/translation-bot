from types import SimpleNamespace

from utils import net


class FakeResponse:
    def __init__(self, status_code=200, text="ok", content=b"ok"):
        self.status_code = status_code
        self.text = text
        self.content = content


def make_session(monkeypatch, plain_side_effect=None, cloud_side_effect=None,
                 max_cloud_attempts=5):
    session = net.CrawlSession(max_cloud_attempts=max_cloud_attempts)

    plain_calls = []
    def fake_plain_get(url, headers=None, timeout=None, **kw):
        plain_calls.append(url)
        if plain_side_effect is not None:
            return plain_side_effect(url)
        return FakeResponse(200, "ok")
    monkeypatch.setattr(session._session, "get", fake_plain_get)

    cloud_calls = []
    def fake_cloud_session(recreate=False):
        fake = SimpleNamespace()
        def fake_get(url, headers=None, timeout=None, **kw):
            cloud_calls.append(url)
            if cloud_side_effect is not None:
                return cloud_side_effect(url, len(cloud_calls))
            return FakeResponse(200, "ok")
        fake.get = fake_get
        fake.close = lambda: None
        return fake
    monkeypatch.setattr(session, "_get_cloud_session", fake_cloud_session)
    monkeypatch.setattr(net.time, "sleep", lambda s: None)  # don't actually wait in tests

    return session, plain_calls, cloud_calls


def test_plain_request_succeeds_no_cloudscraper_needed(monkeypatch):
    session, plain_calls, cloud_calls = make_session(monkeypatch)
    result = session.get("https://example.com/chapter-1")
    assert result.ok
    assert not result.used_cloudscraper
    assert cloud_calls == []


def test_plain_404_does_not_trigger_cloudscraper(monkeypatch):
    session, plain_calls, cloud_calls = make_session(
        monkeypatch, plain_side_effect=lambda url: FakeResponse(404, "not found"))
    result = session.get("https://example.com/missing")
    assert not result.ok
    assert not result.blocked
    assert cloud_calls == []


def test_cloudflare_block_escalates_and_succeeds(monkeypatch):
    def plain(url):
        return FakeResponse(503, "Checking your browser before accessing example.com")

    def cloud(url, attempt):
        if attempt < 2:
            return FakeResponse(503, "Checking your browser before accessing")
        return FakeResponse(200, "chapter content")

    session, plain_calls, cloud_calls = make_session(
        monkeypatch, plain_side_effect=plain, cloud_side_effect=cloud)
    result = session.get("https://example.com/protected")
    assert result.ok
    assert result.used_cloudscraper
    assert len(cloud_calls) == 2


def test_gives_up_after_bounded_cloudscraper_attempts(monkeypatch):
    def plain(url):
        return FakeResponse(503, "cf-browser-verification")

    def cloud(url, attempt):
        return FakeResponse(503, "cf-browser-verification")

    session, plain_calls, cloud_calls = make_session(
        monkeypatch, plain_side_effect=plain, cloud_side_effect=cloud, max_cloud_attempts=5)
    result = session.get("https://example.com/stuck")
    assert not result.ok
    assert result.blocked
    assert len(cloud_calls) == 5  # bounded, not infinite


def test_cloudscrape_disabled_skips_escalation(monkeypatch):
    session, plain_calls, cloud_calls = make_session(
        monkeypatch, plain_side_effect=lambda url: FakeResponse(503, "cloudflare ray id"))
    session.allow_cloudscrape = False
    result = session.get("https://example.com/protected")
    assert not result.ok
    assert result.blocked
    assert cloud_calls == []


def test_looks_like_cloudflare_block_detection():
    assert net.looks_like_cloudflare_block(503, "Just a moment...")
    assert net.looks_like_cloudflare_block(403, "cf-browser-verification")
    assert not net.looks_like_cloudflare_block(404, "page not found")
    assert not net.looks_like_cloudflare_block(200, "all good")


def test_fetch_with_retries_returns_none_after_exhausting(monkeypatch):
    monkeypatch.setattr(net.time, "sleep", lambda s: None)

    def always_fail(url, headers=None, timeout=None):
        raise ConnectionError("boom")

    monkeypatch.setattr(net.requests, "get", always_fail)
    result = net.fetch_with_retries("https://example.com/x", retries=3)
    assert result is None


def test_fetch_with_retries_succeeds_eventually(monkeypatch):
    monkeypatch.setattr(net.time, "sleep", lambda s: None)
    attempts = []

    def flaky(url, headers=None, timeout=None):
        attempts.append(1)
        if len(attempts) < 2:
            raise ConnectionError("boom")
        return FakeResponse(200, "ok")

    monkeypatch.setattr(net.requests, "get", flaky)
    result = net.fetch_with_retries("https://example.com/x", retries=3)
    assert result is not None
    assert result.status_code == 200

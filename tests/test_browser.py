import pytest
from selenium.common.exceptions import SessionNotCreatedException

from utils import browser


class FakeDriver:
    def __init__(self):
        self.quit_called = False
        self.page_load_timeout = None
        self.script_timeout = None

    def set_page_load_timeout(self, t):
        self.page_load_timeout = t

    def set_script_timeout(self, t):
        self.script_timeout = t

    def quit(self):
        self.quit_called = True


def test_get_driver_creates_once(monkeypatch):
    created = []

    def fake_chrome(options=None):
        d = FakeDriver()
        created.append(d)
        return d

    monkeypatch.setattr(browser.webdriver, "Chrome", fake_chrome)
    mgr = browser.BrowserManager()
    d1 = mgr.get_driver()
    d2 = mgr.get_driver()
    assert d1 is d2
    assert len(created) == 1


def test_restart_quits_old_driver_before_creating_new(monkeypatch):
    created = []

    def fake_chrome(options=None):
        d = FakeDriver()
        created.append(d)
        return d

    monkeypatch.setattr(browser.webdriver, "Chrome", fake_chrome)
    mgr = browser.BrowserManager()
    first = mgr.get_driver()
    second = mgr.restart(reason="stale session")
    assert first.quit_called is True  # old process fully released, not leaked
    assert second is not first
    assert len(created) == 2


def test_restart_bounded_raises_after_max(monkeypatch):
    def fake_chrome(options=None):
        return FakeDriver()

    monkeypatch.setattr(browser.webdriver, "Chrome", fake_chrome)
    mgr = browser.BrowserManager()
    mgr.get_driver()
    for _ in range(mgr.MAX_RESTARTS):
        mgr.restart(reason="test")
    with pytest.raises(browser.BrowserStartupError):
        mgr.restart(reason="one too many")


def test_session_not_created_exception_retried_then_raises(monkeypatch):
    attempts = []

    def flaky_chrome(options=None):
        attempts.append(1)
        raise SessionNotCreatedException("chromedriver version mismatch")

    monkeypatch.setattr(browser.webdriver, "Chrome", flaky_chrome)
    mgr = browser.BrowserManager()
    with pytest.raises(browser.BrowserStartupError):
        mgr.get_driver()
    assert len(attempts) == mgr.MAX_STARTUP_RETRIES


def test_quit_is_safe_to_call_when_no_driver():
    mgr = browser.BrowserManager()
    mgr.quit()  # should not raise


def test_quit_swallows_driver_errors(monkeypatch):
    def fake_chrome(options=None):
        return FakeDriver()

    monkeypatch.setattr(browser.webdriver, "Chrome", fake_chrome)
    mgr = browser.BrowserManager()
    mgr.get_driver()

    def raising_quit():
        raise RuntimeError("already dead")

    mgr._driver.quit = raising_quit
    mgr.quit()  # should not raise
    assert mgr._driver is None

"""
Headless-browser lifecycle management.

Browser use is a last resort (see cogs/crawler.py) for pages ordinary HTTP
fetching can't obtain. On a t3.micro we can only afford one Chrome
instance at a time, and every restart needs to fully release the old
process - ``driver.close()`` only closes the current window/tab, it does
NOT terminate the browser/driver process. Restarting via close()+new
driver (as the code used to) leaks a Chrome + chromedriver process pair
on every restart. This module always uses ``quit()`` and bounds how many
times a driver will be recreated in a single run.
"""
import logging

from selenium import webdriver
from selenium.common.exceptions import SessionNotCreatedException, WebDriverException

logger = logging.getLogger("crawler.browser")

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/109.0.0.0 Safari/537.36"
)


def build_chrome_options() -> webdriver.ChromeOptions:
    options = webdriver.ChromeOptions()
    # "--headless=new" is the modern headless mode; falls back cleanly on
    # older Chrome/Chromium builds still shipping the legacy flag name.
    options.add_argument("--headless=new")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--disable-extensions")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    # Reduces memory footprint further on a t3.micro.
    options.add_argument("--disable-background-networking")
    options.add_argument("--disable-default-apps")
    options.add_argument("--disable-sync")
    options.add_argument(f"user-agent={DEFAULT_USER_AGENT}")
    return options


class BrowserStartupError(RuntimeError):
    """Raised when Chrome/chromedriver could not be started after
    exhausting bounded retries (e.g. persistent SessionNotCreatedException
    from a missing/mismatched chromedriver binary)."""


class BrowserManager:
    """Owns at most one headless Chrome instance at a time and hands it
    out to callers. Recreates it only when a caller reports it unhealthy
    (via ``restart``), and always fully quits the old process first."""

    MAX_RESTARTS = 3
    MAX_STARTUP_RETRIES = 2

    def __init__(self, page_load_timeout: float = 30, script_timeout: float = 15):
        self._driver = None
        self._restarts = 0
        self.page_load_timeout = page_load_timeout
        self.script_timeout = script_timeout

    def _create(self):
        last_exc = None
        for attempt in range(1, self.MAX_STARTUP_RETRIES + 1):
            try:
                driver = webdriver.Chrome(options=build_chrome_options())
                driver.set_page_load_timeout(self.page_load_timeout)
                driver.set_script_timeout(self.script_timeout)
                return driver
            except SessionNotCreatedException as e:
                last_exc = e
                logger.warning(
                    "[browser] SessionNotCreatedException starting Chrome (attempt %d/%d): %s. "
                    "This usually means the installed chromedriver version doesn't match the "
                    "installed Chrome/Chromium version on this host.",
                    attempt, self.MAX_STARTUP_RETRIES, e)
            except WebDriverException as e:
                last_exc = e
                logger.warning("[browser] WebDriverException starting Chrome (attempt %d/%d): %s",
                               attempt, self.MAX_STARTUP_RETRIES, e)
        raise BrowserStartupError(f"Could not start headless Chrome: {last_exc}") from last_exc

    def get_driver(self):
        """Returns the current driver, starting one if none exists yet."""
        if self._driver is None:
            logger.info("[browser] starting headless Chrome")
            self._driver = self._create()
        return self._driver

    def restart(self, reason: str = ""):
        """Fully quits the current driver (if any) and starts a fresh one,
        bounded by MAX_RESTARTS. Raises BrowserStartupError once that
        bound is exceeded so callers can stop retrying and report a clear
        error instead of restarting indefinitely."""
        self.quit()
        self._restarts += 1
        if self._restarts > self.MAX_RESTARTS:
            logger.warning("[browser] exceeded max restarts (%d) - giving up. last reason: %s",
                           self.MAX_RESTARTS, reason)
            raise BrowserStartupError(
                f"Headless browser failed {self.MAX_RESTARTS} times in this run: {reason}")
        logger.info("[browser] restarting Chrome (%d/%d): %s", self._restarts, self.MAX_RESTARTS, reason)
        self._driver = self._create()
        return self._driver

    def quit(self):
        """Fully terminates the browser/driver process, if one is running.
        Safe to call multiple times."""
        if self._driver is not None:
            try:
                self._driver.quit()
            except Exception as e:
                logger.info("[browser] error while quitting driver (ignored): %s", e)
            finally:
                self._driver = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.quit()
        return False

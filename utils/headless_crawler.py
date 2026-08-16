"""
Improved Headless Crawling Implementation for EC2 Environments

This module provides robust, EC2-optimized headless crawling with:
- Smart Chrome driver configuration
- Intelligent page loading strategies
- Automatic error recovery
- Navigation loop detection
- Dynamic content extraction
- Resource management
- Structured logging
"""

import asyncio
import logging
import time
import random
import hashlib
from typing import Optional, Dict, List, Tuple, Set
from urllib.parse import urljoin
from dataclasses import dataclass, field
from datetime import datetime

from selenium import webdriver
from selenium.webdriver.support.wait import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.by import By
from selenium.common.exceptions import (
    TimeoutException,
    WebDriverException,
    SessionNotCreatedException,
)
from bs4 import BeautifulSoup


class HeadlessCrawlerLogger:
    """Structured logging for headless crawling operations."""

    def __init__(self, bot_logger: Optional[logging.Logger] = None):
        """Initialize logger, fallback to print if no logger available."""
        self.logger = bot_logger or self._get_default_logger()

    @staticmethod
    def _get_default_logger() -> logging.Logger:
        """Get or create default logger."""
        logger = logging.getLogger("HeadlessCrawler")
        if not logger.handlers:
            handler = logging.StreamHandler()
            formatter = logging.Formatter(
                '%(asctime)s - HeadlessCrawler - %(levelname)s - %(message)s'
            )
            handler.setFormatter(formatter)
            logger.addHandler(handler)
            logger.setLevel(logging.INFO)
        return logger

    def info(self, message: str, **kwargs):
        """Log info level."""
        self.logger.info(f"{message} | {self._format_context(**kwargs)}")

    def warning(self, message: str, **kwargs):
        """Log warning level."""
        self.logger.warning(f"{message} | {self._format_context(**kwargs)}")

    def error(self, message: str, **kwargs):
        """Log error level."""
        self.logger.error(f"{message} | {self._format_context(**kwargs)}")

    def debug(self, message: str, **kwargs):
        """Log debug level."""
        self.logger.debug(f"{message} | {self._format_context(**kwargs)}")

    @staticmethod
    def _format_context(**kwargs) -> str:
        """Format context information for logging."""
        if not kwargs:
            return ""
        return " | ".join(f"{k}={v}" for k, v in kwargs.items())


@dataclass
class NavigationState:
    """Tracks navigation state to detect loops and duplicates."""
    visited_urls: Set[str] = field(default_factory=set)
    url_sequence: List[str] = field(default_factory=list)
    url_occurrence_count: Dict[str, int] = field(default_factory=dict)
    redirect_count: int = 0
    last_url: Optional[str] = None
    session_id: str = ""

    def __post_init__(self):
        """Initialize session ID."""
        if not self.session_id:
            self.session_id = hashlib.md5(
                f"{time.time()}{random.random()}".encode()
            ).hexdigest()[:8]

    def add_url(self, url: str) -> bool:
        """
        Track URL, return False if duplicate/loop detected.

        Args:
            url: URL to track

        Returns:
            True if URL is new, False if duplicate/loop detected
        """
        normalized = self._normalize_url(url)

        if not normalized:
            return False

        # Check for immediate duplicate (same URL twice)
        if normalized == self.last_url:
            self.redirect_count += 1
            return False

        # Check for redirect loop (same URL 3+ times)
        self.url_occurrence_count[normalized] = self.url_occurrence_count.get(normalized, 0) + 1
        if self.url_occurrence_count[normalized] > 2:
            return False

        # Check for circular pattern (A->B->A->B)
        if len(self.url_sequence) >= 2:
            if normalized == self.url_sequence[-2]:
                self.redirect_count += 1
                if self.redirect_count > 3:
                    return False

        self.visited_urls.add(normalized)
        self.url_sequence.append(normalized)
        self.last_url = normalized
        return True

    @staticmethod
    def _normalize_url(url: str) -> str:
        """Normalize URL for robust comparison."""
        if not url:
            return ""

        try:
            from urllib.parse import urlparse, parse_qs, urlencode

            # Parse URL
            parsed = urlparse(url)

            if not parsed.scheme or not parsed.netloc:
                # Invalid URL, skip normalization
                return url.rstrip('/').split('#')[0]

            # Normalize components
            scheme = parsed.scheme.lower()
            netloc = parsed.netloc.lower()
            path = parsed.path.rstrip('/')

            # Reconstruct base URL
            normalized = f"{scheme}://{netloc}{path}"

            # Filter tracking parameters from query string
            if parsed.query:
                tracking_prefixes = ('utm_', 'fbclid', 'gclid', 'msclkid', 'mc_')
                qs = parse_qs(parsed.query)
                filtered = {
                    k: v for k, v in qs.items()
                    if not any(k.lower().startswith(tp) for tp in tracking_prefixes)
                }

                if filtered:
                    # Preserve essential query parameters
                    normalized += f"?{urlencode(filtered, doseq=True)}"

            return normalized
        except Exception:
            # Fallback to simple normalization
            url = url.rstrip('/').split('#')[0]
            return url

    def detect_infinite_loop(self) -> bool:
        """
        Detect if we're in an infinite loop.

        Returns:
            True if loop detected (same URL sequence repeating)
        """
        if len(self.url_sequence) < 4:
            return False

        # Check for repeating pattern
        recent = self.url_sequence[-4:]
        earlier = self.url_sequence[-8:-4] if len(self.url_sequence) >= 8 else []

        return recent == earlier and len(earlier) > 0

    def reset(self):
        """Reset navigation state for new crawl."""
        self.visited_urls.clear()
        self.url_sequence.clear()
        self.url_occurrence_count.clear()
        self.redirect_count = 0
        self.last_url = None
        self.session_id = hashlib.md5(
            f"{time.time()}{random.random()}".encode()
        ).hexdigest()[:8]


class SmartWaitStrategy:
    """Intelligent page loading strategies for dynamic content."""

    DEFAULT_TIMEOUT = 15
    SHORT_TIMEOUT = 5
    LONG_TIMEOUT = 30

    def __init__(self, driver_manager, logger: HeadlessCrawlerLogger):
        """
        Initialize wait strategy.

        Args:
            driver_manager: HeadlessDriver instance (not direct driver reference)
            logger: HeadlessCrawlerLogger instance
        """
        self.driver_manager = driver_manager
        self.logger = logger

    @property
    def driver(self):
        """Get current driver from manager (avoids stale reference)."""
        return self.driver_manager.driver

    async def wait_for_page_load(
        self,
        timeout: int = DEFAULT_TIMEOUT,
        content_selector: Optional[str] = None,
    ) -> bool:
        """
        Wait for page to fully load with unified timeout budget.

        This method employs multiple waiting strategies with a SHARED timeout:
        1. document.readyState === 'complete'
        2. DOM element (body) presence
        3. Content visibility (if selector provided)
        4. Network idle check (jQuery + Performance API)

        IMPORTANT: Timeout is shared across all strategies. The method returns
        as soon as any strategy succeeds OR all strategies timeout.

        Args:
            timeout: Maximum total wait time in seconds (shared budget)
            content_selector: Optional CSS selector for main content

        Returns:
            True if page loaded (any strategy succeeded), False otherwise
        """
        start_time = time.time()
        remaining_timeout = timeout

        try:
            # Strategy 1: Wait for document.readyState == 'complete'
            self.logger.debug(
                "Waiting for document.readyState",
                timeout=remaining_timeout,
            )
            WebDriverWait(self.driver, remaining_timeout).until(
                lambda d: d.execute_script("return document.readyState") == "complete"
            )
            self.logger.debug("Strategy 1 (readyState) succeeded")
            return True

        except TimeoutException:
            self.logger.debug("Strategy 1 (readyState) timeout")
        except WebDriverException:
            return False

        # Update remaining time budget
        elapsed = time.time() - start_time
        remaining_timeout = max(1, timeout - elapsed)

        try:
            # Strategy 2: Wait for body element to exist
            WebDriverWait(self.driver, min(2, remaining_timeout)).until(
                EC.presence_of_element_located((By.TAG_NAME, "body"))
            )
            self.logger.debug("Strategy 2 (body element) succeeded")
        except TimeoutException:
            self.logger.debug("Strategy 2 (body element) timeout")
        except WebDriverException:
            pass

        # Update remaining time budget
        elapsed = time.time() - start_time
        remaining_timeout = max(1, timeout - elapsed)

        try:
            # Strategy 3: Wait for content to be visible
            if content_selector and remaining_timeout > 0:
                WebDriverWait(self.driver, remaining_timeout).until(
                    EC.visibility_of_element_located((By.CSS_SELECTOR, content_selector))
                )
                self.logger.debug(
                    "Strategy 3 (content visibility) succeeded",
                    selector=content_selector,
                )
            else:
                # Wait for any meaningful content (body visible)
                WebDriverWait(self.driver, min(2, remaining_timeout)).until(
                    EC.visibility_of_element_located((By.TAG_NAME, "body"))
                )

        except TimeoutException:
            self.logger.debug("Strategy 3 (content visibility) timeout")
        except WebDriverException:
            pass

        # Update remaining time budget
        elapsed = time.time() - start_time
        remaining_timeout = max(1, timeout - elapsed)

        try:
            # Strategy 4: Wait for network idle (best effort, short timeout)
            await self._wait_network_idle(timeout=min(2, remaining_timeout))

        except Exception as e:
            self.logger.debug("Network idle check failed", error=str(e))

        elapsed = time.time() - start_time
        self.logger.info(
            "Page load completed",
            elapsed_seconds=f"{elapsed:.2f}",
            timeout=timeout,
        )
        return True

    async def _wait_network_idle(self, timeout: int = 5) -> bool:
        """
        Wait for network requests to complete (best effort).

        Args:
            timeout: Maximum wait time

        Returns:
            True if network idle detected
        """
        try:
            # Check pending AJAX requests (jQuery)
            WebDriverWait(self.driver, timeout).until(
                lambda d: d.execute_script(
                    "return (typeof jQuery === 'undefined' || jQuery.active === 0)"
                )
            )
            self.logger.debug("Network idle (jQuery) detected")
            return True
        except TimeoutException:
            self.logger.debug("jQuery network idle timeout")
        except Exception as e:
            self.logger.debug("jQuery network check failed", error=str(e))

        try:
            # Check for fetch/async operations via performance API
            WebDriverWait(self.driver, timeout).until(
                lambda d: d.execute_script(
                    "return performance.getEntriesByType('resource').length === 0 "
                    "|| performance.timing.loadEventEnd > 0"
                )
            )
            self.logger.debug("Network idle (performance API) detected")
            return True
        except TimeoutException:
            self.logger.debug("Performance API network idle timeout")
        except Exception as e:
            self.logger.debug("Performance API network check failed", error=str(e))

        return False

    async def wait_for_content_stable(
        self,
        timeout: int = 1,
        stability_threshold: float = 0.5,
    ) -> bool:
        """
        Simple wait for content to stabilize via fixed sleep.

        NOTE: This does NOT actually verify content stability to avoid expensive
        DOM serialization operations. It simply waits for a fixed time to allow
        late-loaded content to render.

        Args:
            timeout: Maximum wait time
            stability_threshold: Minimum stable period (ignored, uses timeout)

        Returns:
            True (always succeeds)
        """
        # Simple fixed wait instead of expensive polling
        actual_wait = min(timeout, stability_threshold)
        await asyncio.sleep(actual_wait)
        self.logger.debug(
            "Content stabilization wait completed",
            waited_seconds=f"{actual_wait:.2f}",
        )
        return True


class ContentExtractor:
    """Extracts content from loaded pages with validation."""

    def __init__(self, logger: HeadlessCrawlerLogger):
        """Initialize extractor."""
        self.logger = logger

    async def extract_html(
        self,
        driver,
        min_length: int = 100,
    ) -> Optional[str]:
        """
        Extract and validate HTML from page.

        Args:
            driver: Selenium driver
            min_length: Minimum content length to consider valid

        Returns:
            HTML string or None if invalid
        """
        try:
            html = driver.page_source
        except WebDriverException as e:
            self.logger.error("Failed to get page_source", error=str(e))
            return None

        if not html or len(html) < min_length:
            self.logger.warning(
                "HTML content too short or empty",
                length=len(html) if html else 0,
                min_required=min_length,
            )
            return None

        # Basic validation
        if "<html" not in html.lower() and "<body" not in html.lower():
            self.logger.warning("Invalid HTML structure")
            return None

        self.logger.debug("HTML extracted successfully", length=len(html))
        return html

    async def extract_next_link(
        self,
        soup: BeautifulSoup,
        current_url: str,
        selector_xpath: Optional[str] = None,
    ) -> Optional[str]:
        """
        Extract next chapter link from page with multiple strategies.

        Args:
            soup: BeautifulSoup object
            current_url: Current page URL
            selector_xpath: Optional selector for next button

        Returns:
            Next URL or None
        """
        if not soup:
            return None

        next_link = None

        # Strategy 1: Use provided selector (only valid CSS selectors)
        if selector_xpath:
            try:
                if "::attr(href)" in selector_xpath:
                    selector = selector_xpath.replace("::attr(href)", "").strip()
                else:
                    selector = selector_xpath

                element = soup.select_one(selector)
                if element and element.get("href"):
                    next_link = element.get("href")
                    self.logger.debug(
                        "Found next link via provided selector",
                        selector=selector[:100],
                    )
            except Exception as e:
                self.logger.debug(
                    "Provided selector extraction failed",
                    selector=selector_xpath[:100] if selector_xpath else None,
                    error=str(e),
                )

        # Strategy 2: CSS-based selectors (valid for BeautifulSoup)
        if not next_link:
            patterns = [
                ('a[rel="next"]', "rel=next"),
                ('a.next', "class=next"),
                ('a.next-chapter', "class=next-chapter"),
                ('a[aria-label*="next"]', "aria-label"),
            ]

            for selector, pattern_name in patterns:
                try:
                    element = soup.select_one(selector)
                    if element and element.get("href"):
                        next_link = element.get("href")
                        self.logger.debug(
                            "Found next link via CSS pattern",
                            pattern=pattern_name,
                        )
                        break
                except Exception as e:
                    self.logger.debug(
                        "CSS pattern failed",
                        pattern=pattern_name,
                        error=str(e),
                    )

        # Strategy 3: Text-based detection for common next button text
        if not next_link:
            next_link = self._extract_next_by_text(soup)

        if next_link:
            # Convert relative to absolute URL
            next_link = urljoin(current_url, next_link)
            self.logger.debug(
                "Next link extracted",
                url=next_link[:100],
            )
            return next_link

        self.logger.debug("No next link found", url=current_url[:100])
        return None

    def _extract_next_by_text(self, soup: BeautifulSoup) -> Optional[str]:
        """
        Extract next link by looking for text content.

        Args:
            soup: BeautifulSoup object

        Returns:
            URL or None
        """
        try:
            # Common text patterns for next button in multiple languages
            next_patterns = [
                'next', 'next chapter', '>',
                '下一章', '下章',  # Chinese
                '다음', '다음장',  # Korean
                'следующий', 'далее',  # Russian
                'suivant', 'prochain chapitre',  # French
            ]

            for link in soup.find_all('a', limit=1000):  # Limit search
                text = link.get_text(strip=True).lower()
                if any(keyword in text for keyword in next_patterns):
                    if link.get("href"):
                        self.logger.debug(
                            "Found next link by text",
                            text=text[:50],
                        )
                        return link.get("href")

            return None
        except Exception as e:
            self.logger.debug("Text-based extraction failed", error=str(e))
            return None


class RecoveryManager:
    """Manages driver recovery from various failure modes."""

    MAX_RETRIES = 3
    RETRY_BACKOFF_FACTOR = 2
    INITIAL_RETRY_DELAY = 1
    MAX_FAILURE_HISTORY = 100  # Prevent unbounded memory growth

    def __init__(self, logger: HeadlessCrawlerLogger):
        """Initialize recovery manager."""
        self.logger = logger
        self.failure_count = 0
        self.last_failure = None
        self.failure_reasons = []

    def record_failure(self, reason: str, url: Optional[str] = None):
        """
        Record a failure for recovery tracking.

        Args:
            reason: Description of failure
            url: URL where failure occurred
        """
        self.failure_count += 1
        self.last_failure = datetime.now()
        self.failure_reasons.append(reason)

        # Keep only last N failures to prevent memory leak
        if len(self.failure_reasons) > self.MAX_FAILURE_HISTORY:
            self.failure_reasons.pop(0)

        self.logger.warning(
            "Failure recorded",
            failure_count=self.failure_count,
            reason=reason,
            url=url[:100] if url else None,
        )

    def get_retry_delay(self) -> float:
        """
        Calculate exponential backoff delay for retry.

        Returns:
            Delay in seconds
        """
        if self.failure_count == 0:
            return 0

        delay = self.INITIAL_RETRY_DELAY * (
            self.RETRY_BACKOFF_FACTOR ** (self.failure_count - 1)
        )
        # Cap at 30 seconds
        delay = min(delay, 30)
        # Add jitter to prevent thundering herd
        delay += random.uniform(0, delay * 0.1)

        return delay

    def should_retry(self) -> bool:
        """Check if we should retry."""
        return self.failure_count < self.MAX_RETRIES

    def reset(self):
        """Reset failure tracking."""
        self.failure_count = 0
        self.last_failure = None
        self.failure_reasons = []  # MUST clear to prevent memory leak


class HeadlessDriver:
    """
    Manages Selenium Chrome driver lifecycle with EC2 optimization.

    Provides:
    - Optimized Chrome configuration for EC2
    - Automation detection masking
    - Resource management
    - Automatic recovery from crashes
    """

    # EC2 optimizations
    EC2_ARGS = [
        "--headless=new",  # Modern headless mode
        "--no-sandbox",  # Required in containers
        "--disable-dev-shm-usage",  # EC2 has limited /dev/shm
        "--disable-gpu",  # Reduce memory usage
        "--disable-background-networking",
        "--disable-renderer-backgrounding",
        "--disable-popup-blocking",
        "--disable-notifications",
        "--disable-blink-features=AutomationControlled",
        "--start-maximized",
        "--disable-extensions",
        "--disable-plugins",
        "--disable-images",  # Optional: disable images to save memory
        "--disable-default-apps",
        "--disable-preconnect",
        "--disable-background-timer-throttling",
        "--disable-backgrounding-occluded-windows",
        "--disable-breakpad",
        "--disable-client-side-phishing-detection",
        "--disable-hang-monitor",
        "--disable-prompt-on-repost",
        "--disable-sync",
        "--enable-automation",
    ]

    # User agents for masking
    USER_AGENTS = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
        "Mozilla/5.0 (X11; Linux x86_64; rv:121.0) Gecko/20100101 Firefox/121.0",
    ]

    def __init__(self, logger: HeadlessCrawlerLogger, keep_images: bool = False):
        """
        Initialize driver manager.

        Args:
            logger: HeadlessCrawlerLogger instance
            keep_images: Whether to load images (uses more memory)
        """
        self.logger = logger
        self.driver = None
        self.keep_images = keep_images
        self.creation_time = None
        self.request_count = 0

    async def create_driver(self) -> Optional[webdriver.Chrome]:
        """
        Create optimized Chrome driver.

        Returns:
            Chrome driver or None on failure
        """
        try:
            options = webdriver.ChromeOptions()

            # Add EC2 optimization arguments
            for arg in self.EC2_ARGS:
                options.add_argument(arg)

            # Randomize user agent
            user_agent = random.choice(self.USER_AGENTS)
            options.add_argument(f"user-agent={user_agent}")

            # Prevent detection
            options.add_experimental_option("excludeSwitches", ["enable-automation"])
            options.add_experimental_option("useAutomationExtension", False)

            # Disable images if not needed (saves memory)
            if not self.keep_images:
                prefs = {
                    "profile.managed_default_content_settings.images": 2
                }
                options.add_experimental_option("prefs", prefs)

            # Create driver
            self.driver = webdriver.Chrome(options=options)
            self.creation_time = datetime.now()
            self.request_count = 0

            self.logger.info(
                "Chrome driver created",
                user_agent=user_agent[:50],
            )

            return self.driver

        except SessionNotCreatedException as e:
            self.logger.error(
                "Failed to create Chrome driver - session creation error",
                error=str(e),
            )
            return None

        except Exception as e:
            self.logger.error(
                "Failed to create Chrome driver",
                error=str(e),
                error_type=type(e).__name__,
            )
            return None

    async def close_driver(self, force_kill: bool = False):
        """
        Safely close driver and cleanup resources.

        Args:
            force_kill: If True, force-kill Chrome process if graceful close fails
        """
        if not self.driver:
            return

        try:
            # Try graceful shutdown
            self.driver.quit()
            self.logger.info("Driver closed successfully")
        except Exception as e:
            self.logger.warning("Graceful driver close failed", error=str(e))

            # Force kill if requested
            if force_kill and hasattr(self.driver, 'service'):
                try:
                    import os
                    import signal
                    if hasattr(self.driver.service, 'process') and self.driver.service.process:
                        pid = self.driver.service.process.pid
                        os.kill(pid, signal.SIGKILL)
                        self.logger.warning("Chrome process force-killed", pid=pid)
                except Exception as kill_error:
                    self.logger.error("Force kill failed", error=str(kill_error))
        finally:
            self.driver = None

    async def navigate_to(self, url: str, timeout: int = 15) -> bool:
        """
        Navigate to URL with timeout and error handling.

        Args:
            url: URL to navigate to
            timeout: Navigation timeout in seconds

        Returns:
            True if successful
        """
        if not self.driver:
            return False

        try:
            # Set page load timeout
            self.driver.set_page_load_timeout(timeout)
            self.driver.get(url)
            self.request_count += 1
            self.logger.debug(
                "Navigation successful",
                url=url[:100],
                request_count=self.request_count,
            )
            return True

        except TimeoutException:
            self.logger.error(
                "Page load timeout",
                url=url[:100],
                timeout=timeout,
            )
            return False

        except WebDriverException as e:
            self.logger.error(
                "Navigation failed",
                url=url[:100],
                error=str(e),
                error_type=type(e).__name__,
            )
            return False

    def is_alive(self) -> bool:
        """Check if driver is still responsive."""
        if not self.driver:
            return False

        try:
            # Test driver responsiveness by accessing current_url
            _ = self.driver.current_url
            return True
        except:
            return False

    def should_restart(self) -> bool:
        """
        Check if driver should be restarted based on multiple factors.

        Returns:
            True if driver should be restarted
        """
        # Memory pressure check
        try:
            import psutil
            memory = psutil.virtual_memory()
            if memory.percent > 80:  # 80% memory used
                self.logger.warning(
                    "High memory pressure, restarting driver",
                    memory_percent=f"{memory.percent:.1f}%",
                )
                return True
        except ImportError:
            pass  # psutil not available, skip check
        except Exception as e:
            self.logger.debug("Memory check failed", error=str(e))

        # Driver responsiveness check
        if not self.is_alive():
            self.logger.warning("Driver unresponsive, needs restart")
            return True

        # Restart after many requests (memory leak prevention)
        if self.request_count > 50:
            self.logger.info(
                "Driver request limit reached, restarting",
                request_count=self.request_count,
            )
            return True

        # Restart after timeout period
        if self.creation_time:
            uptime = (datetime.now() - self.creation_time).total_seconds()
            if uptime > 600:  # 10 minutes
                self.logger.info("Driver uptime limit reached, restarting", uptime=f"{uptime:.0f}s")
                return True

        return False


class HeadlessCrawler:
    """
    Main headless crawler coordinator.

    Orchestrates all components for robust, EC2-optimized crawling.
    """

    def __init__(
        self,
        bot_logger: Optional[logging.Logger] = None,
        keep_images: bool = False,
    ):
        """
        Initialize headless crawler.

        Args:
            bot_logger: Optional logger from bot
            keep_images: Whether to load images
        """
        self.logger = HeadlessCrawlerLogger(bot_logger)
        self.driver_manager = HeadlessDriver(self.logger, keep_images)
        self.wait_strategy = None
        self.content_extractor = ContentExtractor(self.logger)
        self.recovery_manager = RecoveryManager(self.logger)
        self.nav_state = NavigationState()
        self.correlation_id = hashlib.md5(
            f"{time.time()}{random.random()}".encode()
        ).hexdigest()[:8]

    async def initialize(self) -> bool:
        """
        Initialize crawler (create driver).

        Returns:
            True if successful
        """
        self.driver_manager.driver = await self.driver_manager.create_driver()
        if self.driver_manager.driver:
            self.wait_strategy = SmartWaitStrategy(
                self.driver_manager,
                self.logger,
            )
            self.logger.info(
                "Crawler initialized",
                correlation_id=self.correlation_id,
            )
            return True

        self.logger.error("Failed to initialize crawler", correlation_id=self.correlation_id)
        return False

    async def reset_for_new_crawl(self):
        """
        Reset crawler state for new crawl and cleanup old driver.

        This should be called before starting a new crawl to:
        - Clear navigation state (prevent cross-crawl contamination)
        - Reset recovery manager
        - Generate new correlation ID
        - Close old driver
        """
        self.logger.info("Resetting crawler for new crawl", old_correlation_id=self.correlation_id)

        # Generate new correlation ID for new crawl
        self.correlation_id = hashlib.md5(
            f"{time.time()}{random.random()}".encode()
        ).hexdigest()[:8]

        # Reset state
        self.nav_state.reset()
        self.recovery_manager.reset()

        # Close old driver if exists
        if self.driver_manager.driver:
            await self.driver_manager.close_driver()

        self.logger.info("Crawler reset completed", correlation_id=self.correlation_id)

    async def fetch_page(
        self,
        url: str,
        content_selector: Optional[str] = None,
        timeout: int = 15,
    ) -> Optional[Tuple[str, str]]:
        """
        Fetch page with automatic recovery.

        Args:
            url: URL to fetch
            content_selector: Optional CSS selector for main content
            timeout: Page load timeout

        Returns:
            (html, final_url) or None on failure
        """
        attempt = 0

        while attempt < self.recovery_manager.MAX_RETRIES:
            try:
                # Check if driver needs restart
                if self.driver_manager.should_restart():
                    await self.driver_manager.close_driver()
                    if not await self.initialize():
                        self.recovery_manager.record_failure(
                            "Driver restart failed",
                            url,
                        )
                        attempt += 1
                        continue

                # Navigate to URL
                if not await self.driver_manager.navigate_to(url, timeout):
                    self.recovery_manager.record_failure("Navigation failed", url)
                    attempt += 1
                    await asyncio.sleep(self.recovery_manager.get_retry_delay())
                    continue

                # Wait for page to load
                if not await self.wait_strategy.wait_for_page_load(
                    timeout=timeout,
                    content_selector=content_selector,
                ):
                    self.logger.warning("Page load timeout, trying to extract anyway")

                # Wait for content to stabilize
                await self.wait_strategy.wait_for_content_stable(timeout=5)

                # Extract HTML
                html = await self.content_extractor.extract_html(
                    self.driver_manager.driver
                )

                if not html:
                    self.recovery_manager.record_failure("HTML extraction failed", url)
                    attempt += 1
                    await asyncio.sleep(self.recovery_manager.get_retry_delay())
                    continue

                # Success
                self.recovery_manager.reset()
                final_url = self.driver_manager.driver.current_url

                self.logger.info(
                    "Page fetched successfully",
                    url=url[:100],
                    final_url=final_url[:100],
                    attempt=attempt + 1,
                )

                return (html, final_url)

            except Exception as e:
                self.logger.error(
                    "Fetch page error",
                    url=url[:100],
                    error=str(e),
                    attempt=attempt + 1,
                )
                self.recovery_manager.record_failure(str(e), url)
                attempt += 1

                if attempt < self.recovery_manager.MAX_RETRIES:
                    delay = self.recovery_manager.get_retry_delay()
                    self.logger.info(
                        "Retrying after delay",
                        delay=f"{delay:.2f}s",
                        attempt=attempt,
                    )
                    await asyncio.sleep(delay)

        self.logger.error(
            "Max retries exceeded",
            url=url[:100],
            attempts=attempt,
        )
        return None

    async def extract_next_link(
        self,
        html: str,
        current_url: str,
        selector_xpath: Optional[str] = None,
    ) -> Optional[str]:
        """
        Extract next chapter link from HTML.

        Args:
            html: Page HTML
            current_url: Current page URL
            selector_xpath: Optional selector

        Returns:
            Next URL or None
        """
        try:
            soup = BeautifulSoup(html, "html.parser")
            next_link = await self.content_extractor.extract_next_link(
                soup,
                current_url,
                selector_xpath,
            )

            if next_link:
                # Check for navigation issues
                if not self.nav_state.add_url(next_link):
                    self.logger.warning(
                        "Navigation loop detected",
                        url=next_link[:100],
                        session=self.nav_state.session_id,
                    )
                    return None

                if self.nav_state.detect_infinite_loop():
                    self.logger.error(
                        "Infinite loop pattern detected",
                        session=self.nav_state.session_id,
                    )
                    return None

            return next_link

        except Exception as e:
            self.logger.error(
                "Next link extraction error",
                error=str(e),
                url=current_url[:100],
            )
            return None

    async def cleanup(self):
        """Cleanup resources."""
        await self.driver_manager.close_driver()
        self.logger.info("Crawler cleanup completed")


# Legacy wrapper function for backward compatibility
async def get_driver_async(
    bot_logger: Optional[logging.Logger] = None,
) -> Optional[webdriver.Chrome]:
    """
    Create a Chrome driver asynchronously.

    This is a backward-compatible wrapper for the improved driver creation.

    Args:
        bot_logger: Optional logger

    Returns:
        Chrome driver or None
    """
    driver_mgr = HeadlessDriver(HeadlessCrawlerLogger(bot_logger))
    return await driver_mgr.create_driver()


def get_driver(
    bot_logger: Optional[logging.Logger] = None,
) -> Optional[webdriver.Chrome]:
    """
    Create a Chrome driver (synchronous version).

    Backward-compatible replacement for the original get_driver().

    Args:
        bot_logger: Optional logger

    Returns:
        Chrome driver or None
    """
    logger = HeadlessCrawlerLogger(bot_logger)
    driver_mgr = HeadlessDriver(logger)

    try:
        # Create driver synchronously (run in executor for real async code)
        options = webdriver.ChromeOptions()

        for arg in driver_mgr.EC2_ARGS:
            options.add_argument(arg)

        user_agent = random.choice(driver_mgr.USER_AGENTS)
        options.add_argument(f"user-agent={user_agent}")

        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option("useAutomationExtension", False)

        if not driver_mgr.keep_images:
            prefs = {"profile.managed_default_content_settings.images": 2}
            options.add_experimental_option("prefs", prefs)

        driver = webdriver.Chrome(options=options)
        logger.info("Chrome driver created successfully")
        return driver

    except Exception as e:
        logger.error("Failed to create driver", error=str(e))
        return None


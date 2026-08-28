"""
Centralized BeautifulSoup construction.

Replaces the previously scattered ``BeautifulSoup(data, "html.parser")`` /
``BeautifulSoup(data, "lxml")`` / ``BeautifulSoup(data, "html5lib")`` calls
with one consistent strategy: prefer lxml (fast, lenient with malformed
markup), but always fall back to Python's built-in html.parser if lxml
isn't installed or chokes on a given page. Never raises for malformed
HTML, missing elements, or empty pages - always returns a BeautifulSoup
object (possibly empty).
"""
import logging

from bs4 import BeautifulSoup

logger = logging.getLogger("crawler.parsing")

_LXML_AVAILABLE = None


def _lxml_available() -> bool:
    global _LXML_AVAILABLE
    if _LXML_AVAILABLE is None:
        try:
            import lxml  # noqa: F401
            _LXML_AVAILABLE = True
        except ImportError:
            logger.info("[parsing] lxml not installed, defaulting to html.parser")
            _LXML_AVAILABLE = False
    return _LXML_AVAILABLE


def make_soup(markup, *, from_encoding: str = None, prefer: str = "lxml") -> BeautifulSoup:
    """Build a BeautifulSoup object with a consistent parser strategy and
    graceful fallback. ``markup`` may be str or bytes; ``prefer`` is either
    "lxml" (default) or "html.parser" to skip straight to the builtin
    parser for content known to be simple/well-formed."""
    if markup is None:
        markup = ""

    parsers_to_try = []
    if prefer == "lxml" and _lxml_available():
        parsers_to_try.append("lxml")
    if "html.parser" not in parsers_to_try:
        parsers_to_try.append("html.parser")

    last_exc = None
    for i, parser in enumerate(parsers_to_try):
        try:
            kwargs = {"from_encoding": from_encoding} if from_encoding else {}
            soup = BeautifulSoup(markup, parser, **kwargs)
            if i > 0:
                logger.info("[parsing] fell back to %s parser after %s failed",
                           parser, parsers_to_try[0])
            return soup
        except Exception as e:
            last_exc = e
            logger.info("[parsing] parser %r failed: %s", parser, e)
            continue

    logger.warning("[parsing] all parsers failed (%s); returning an empty document", last_exc)
    return BeautifulSoup("", "html.parser")

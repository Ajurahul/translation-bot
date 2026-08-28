"""
URL normalization and visited-link tracking, used to stop crawlnext from
looping on a "next chapter" link that (due to a trailing slash, a URL
fragment, or scheme/host case) looks different from an already-crawled
link but actually points at the same page.

Deliberately conservative: it does NOT touch the query string or the path
case, since several of the sites in utils/selector.py are case-sensitive
or rely on exact query parameters.
"""
from urllib.parse import urljoin, urlsplit, urlunsplit


def normalize_url(url: str, base: str = None) -> str:
    """Return a normalized form of ``url`` suitable for de-duplication.
    Resolves relative URLs against ``base`` if given, drops the fragment,
    lowercases scheme/host, and strips a single trailing slash from the
    path (except the root ``/``)."""
    if not url:
        return url
    if base:
        url = urljoin(base, url)
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    netloc = parts.netloc.lower()
    path = parts.path
    if len(path) > 1 and path.endswith("/"):
        path = path[:-1]
    return urlunsplit((scheme, netloc, path, parts.query, ""))


class VisitedTracker:
    """Bounded-memory set of normalized URLs visited during one crawl run.
    Used to detect a next-chapter link looping back to a page already
    crawled, instead of relying purely on an iteration-count fallback."""

    def __init__(self, base: str = None):
        self._seen = set()
        self._base = base

    def add(self, url: str) -> str:
        normalized = normalize_url(url, self._base)
        self._seen.add(normalized)
        return normalized

    def seen(self, url: str) -> bool:
        return normalize_url(url, self._base) in self._seen

    def __len__(self):
        return len(self._seen)

    def __contains__(self, url: str) -> bool:
        return self.seen(url)

"""Exception hierarchy + response validation primitives.

The error-page detection/filtering logic here is carried over unchanged
from the previous utils/translate.py implementation (Translator.
_is_error_500_response / _filter_error_text) -- it was already tuned to
avoid the naive `"error" in text` false-positive trap, so it's reused
rather than rewritten.
"""
import re
import typing as t


class TranslationError(Exception):
    """Base class for every error raised by the translation.* package."""


class TransientTranslationError(TranslationError):
    """Retry-worthy failure: HTTP 429/500/502/503/504, timeouts,
    connection errors, or a detected provider error page."""


class PermanentTranslationError(TranslationError):
    """Not retry-worthy: missing dependency, bad configuration, or an
    empty/None response that retrying the same provider won't fix."""


class TranslationFailedError(TranslationError):
    """Raised when a user-selected engine (explicit choice, or the
    resolved Default) exhausts its retries. Never triggers a silent
    fallback to another provider."""


class AllEnginesFailedError(TranslationError):
    """Raised by Auto mode when every candidate engine failed, including
    after the one bounded reset-and-retry pass."""


class InvalidEngineError(TranslationError):
    """Raised when a caller selects an engine id the registry doesn't
    recognize, or that exists but isn't currently available."""


TRANSIENT_STATUS_CODES = frozenset({429, 500, 502, 503, 504})

_ERROR_INDICATORS = (
    "error 500",
    "server error",
    "that's an error",
    "there was an error",
    "please try again later",
    "that's all we know",
    "too many requests",
    "bad request",
    "service unavailable",
)


def is_error_response(translated: t.Sequence[str]) -> bool:
    """Detect a provider error page. Deliberately conservative: a single
    incidental occurrence of a word like "error" inside a legitimate
    translation must NOT trip this -- we require either the specific
    "error 500" marker, or at least two distinct indicator phrases."""
    if not translated:
        return False

    joined = " ".join(str(part) for part in translated).lower()
    joined = joined.replace("\u2019", "'")

    marker_hits = sum(indicator in joined for indicator in _ERROR_INDICATORS)
    return ("error 500" in joined) or (marker_hits >= 2)


def filter_error_text(text: str) -> str:
    """Secondary safety net: strip an error-page fragment out of an
    otherwise-usable string. This must never be relied on to turn a
    fundamentally invalid response into a "successful" translation --
    callers re-validate with is_error_response() after filtering."""
    if not text:
        return text

    error_patterns = [
        r"Error 500.*?(?=\n[A-Za-z\u00C0-\u00FF]|\Z)",
        r"(?:error|server error|that's an error|there was an error|please try again later).*?(?=\n[A-Za-z\u00C0-\u00FF]|\Z)",
    ]

    result = text
    for pattern in error_patterns:
        result = re.sub(pattern, "", result, flags=re.IGNORECASE | re.DOTALL)

    return result.strip()


def is_transient_status(status_code: t.Any) -> bool:
    try:
        return int(status_code) in TRANSIENT_STATUS_CODES
    except (TypeError, ValueError):
        return False


# Quota/rate-limit indicators -- deliberately distinct from
# _ERROR_INDICATORS above. These specifically mean "this provider is out
# of requests for now" rather than "this provider returned an error
# page", which matters to callers that want to stop retrying/racing an
# exhausted provider instead of just backing off and trying again (see
# translation/health.py and TranslationManager's failure handling).
_QUOTA_INDICATORS = (
    "quota",
    "too many requests",
    "toomanyrequests",
    "rate limit",
    "ratelimit",
    "daily limit",
    "limit exceeded",
    # MyMemory returns these as if they were the translation itself
    # rather than raising an HTTP error -- see
    # translation/providers/deep_translator_backend.py.
    "mymemory warning",
    "you used all available free translations",
    "query length limit exceeded",
)


def is_quota_error(value: t.Any) -> bool:
    """True if `value` (an exception, or raw text) looks like a
    quota/rate-limit failure rather than a generic transient one."""
    status = getattr(value, "status_code", None) or getattr(value, "code", None)
    if is_transient_status(status) and int(status) == 429:
        return True

    text = str(value or "").lower()
    return any(indicator in text for indicator in _QUOTA_INDICATORS)


def is_mymemory_quota_warning(text: str) -> bool:
    """MyMemory-specific check used directly on a *successful-looking*
    response body (no exception raised) before it's ever treated as a
    real translation -- see the MyMemory backend in
    deep_translator_backend.py."""
    low = str(text or "").lower()
    return any(
        indicator in low
        for indicator in (
            "mymemory warning",
            "you used all available free translations",
            "query length limit exceeded",
        )
    )

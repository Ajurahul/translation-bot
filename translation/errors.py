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

    # Only strip real error-page phrases. The previous version also matched
    # the bare substring "error" with no word boundary, so ordinary novel
    # text like "fled in terror." was cut down to "fled in t" -- and because
    # the match ran with DOTALL up to a next-line start in [A-Za-z], text in
    # any other script (Hindi, Korean, Chinese...) was deleted all the way to
    # the end of the chunk/story. Word boundaries + "never leave the line the
    # phrase is on" fix all three. (An earlier attempt at this stopped at the
    # next line starting with a letter, which failed to strip error pages
    # followed by a blank line, a digit or a quote.)
    #
    # Only whole LINES made up of error-page sentences are removed. This runs
    # unconditionally on every translated chunk and on the final story, so a
    # novel line like "Please try again later, she said." must survive; a
    # mixed line that really is a provider error is left in place, where
    # is_error_response()/validation rejects the response and it is retried.
    sentence = r"(?:that['\u2019]s an error|there was an error|please try again later|that['\u2019]s all we know|server error)[.!]?"
    error_patterns = [
        r"^[ \t]*Error 500\b[^\n\r]*[ \t\r]*$",
        r"^[ \t]*(?:\d{3}\.[ \t]*)?(?:" + sentence + r"[ \t]*)+[ \t\r]*$",
    ]

    result = text
    for pattern in error_patterns:
        result = re.sub(pattern, "", result, flags=re.IGNORECASE | re.MULTILINE)

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


# -- provider "soft errors" -------------------------------------------
# Some providers (MyMemory above all) answer a bad *request* with HTTP 200
# and put the complaint in the translation field, e.g.:
#   "'AUTO' IS AN INVALID SOURCE LANGUAGE . EXAMPLE: LANGPAIR=EN|IT USING
#    2 LETTER ISO OR RFC3066 LIKE ZH-CN. ALMOST ALL LANGUAGES SUPPORTED
#    BUT SOME MAY HAVE NO CONTENT"
# Before this check existed, that sentence was accepted as a successful
# translation and written straight into the user's novel.
_PROVIDER_REQUEST_ERRORS = (
    "is an invalid source language",
    "is an invalid target language",
    "invalid language pair",
    "please select two distinct languages",
    "using 2 letter iso or rfc3066",
    "no query specified",
)


def is_provider_request_error(text: t.Any) -> bool:
    """True if `text` is a provider complaining about the *request* (bad
    language code, same source/target...) rather than a translation.
    Retrying the identical request can never fix it."""
    low = str(text or "").lower()
    return any(marker in low for marker in _PROVIDER_REQUEST_ERRORS)


_NONE_LIKE = frozenset({"none", "null", "nan", "undefined"})


def is_none_like(text: t.Any) -> bool:
    """True for None or the *string* forms of it ("None", "null"...).
    Backends call str() on whatever the library returned, so a library
    that returned None used to become the 4-letter word "None" and sail
    through validation -- that is where crawled titles turned into "None"."""
    if text is None:
        return True
    return str(text).strip().lower() in _NONE_LIKE

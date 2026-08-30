"""Shared response-validation helpers for translation backends.

This is the single canonical implementation of "does this look like a
translation, or does it look like an error page / rate-limit response".

`utils.translate.Translator._is_error_500_response` /
`_filter_error_text` (the pre-existing implementation used by the legacy
chapter-translation pipeline) delegate to the functions in this module so
there is exactly one place this logic lives, per Rule 13 (reuse and
improve the existing detector instead of duplicating it).
"""
import re
import typing as t

# Phrases that show up on provider error/interstitial pages rather than in
# genuine translated text. Kept lowercase; matching is done against a
# lowercased, quote-normalized copy of the response.
_ERROR_INDICATORS: t.Tuple[str, ...] = (
    "error 500",
    "server error",
    "that's an error",
    "there was an error",
    "please try again later",
    "that's all we know",
    "too many requests",
    "bad request",
    "rate limit",
    "quota exceeded",
    "service unavailable",
)

_ERROR_TEXT_PATTERNS: t.Tuple[str, ...] = (
    r"Error 500.*?(?=\n[A-Za-zÀ-ÿ]|\Z)",
    # Deliberately does NOT match the bare word "error" -- a legitimate
    # translation (e.g. a chapter about a software bug) can easily
    # contain that word on its own. Only specific multi-word phrases that
    # actually look like a provider error/interstitial page are stripped.
    r"(?:server error|that's an error|there was an error|please try again later).*?(?=\n[A-Za-zÀ-ÿ]|\Z)",
)


def is_error_response(translated: t.Sequence[t.Optional[str]]) -> bool:
    """Detect if a (possibly multi-part) response looks like a provider
    error/interstitial page rather than a real translation.

    Also treats an entirely empty/None response as an error, since a
    successful translation should never be empty for non-empty input.
    """
    if not translated:
        return False

    # None / empty-string entries anywhere in a non-empty response list are
    # themselves suspicious, but we only hard-fail on "the whole thing is
    # empty" here -- callers that care about "empty parts" should check
    # `has_empty_parts` explicitly, since a legitimately-untranslatable
    # blank line in the middle of a chapter is not an error.
    joined = " ".join(str(part) for part in translated if part is not None).lower()
    if not joined.strip():
        return True

    joined = joined.replace("\u2019", "'")

    marker_hits = sum(indicator in joined for indicator in _ERROR_INDICATORS)
    return ("error 500" in joined) or (marker_hits >= 2)


def has_empty_parts(translated: t.Sequence[t.Optional[str]]) -> bool:
    """True if any individual item in a batch response is None/blank."""
    return any(part is None or not str(part).strip() for part in translated)


def filter_error_text(text: str) -> str:
    """Strip embedded error-page fragments out of otherwise-valid text."""
    if not text:
        return text
    result = text
    for pattern in _ERROR_TEXT_PATTERNS:
        result = re.sub(pattern, "", result, flags=re.IGNORECASE | re.DOTALL)
    return result.strip()


def validate_and_clean(translated: t.List[str]) -> t.List[str]:
    """Raise if the response looks like an error page, otherwise return a
    cleaned copy with any embedded error fragments stripped. Re-checks
    after cleaning so a partially-error payload can never slip through."""
    if is_error_response(translated):
        raise RuntimeError("translation returned an error/rate-limit response body")

    cleaned = [filter_error_text(str(text)) for text in translated]

    if is_error_response(cleaned):
        raise RuntimeError("translation returned an error/rate-limit response body")

    return cleaned

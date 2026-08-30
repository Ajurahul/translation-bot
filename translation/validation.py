"""Validate a raw provider response before it's ever returned to a caller
or written to output. Nothing here is optional -- validate() is always
called by TranslationManager, for every backend, for every call."""
import typing as t

from .errors import (
    PermanentTranslationError,
    TransientTranslationError,
    filter_error_text,
    is_error_response,
)


def validate_translation(original: str, raw: t.Optional[str]) -> str:
    if raw is None:
        raise PermanentTranslationError("translation provider returned no response (None)")

    text = str(raw)
    if not text.strip() and str(original or "").strip():
        raise PermanentTranslationError("translation provider returned an empty response")

    if not is_error_response([text]):
        # Nothing suspicious detected -- return as-is. filter_error_text()
        # is a *secondary* safety net for content that already looked
        # like an error page; running it unconditionally on every clean
        # response risks mangling a legitimate translation that simply
        # happens to contain a word like "error" (its regex has no
        # reliable stopping point short of end-of-string on single-line
        # text), which would turn a valid translation into a false
        # rejection/truncation -- exactly what section 21 forbids.
        return text

    cleaned = filter_error_text(text)
    if not cleaned.strip() or is_error_response([cleaned]):
        raise TransientTranslationError("translation provider returned an error response")

    return cleaned


def validate_translation_batch(
    originals: t.Sequence[str], raw: t.Optional[t.Sequence[str]]
) -> t.List[str]:
    if raw is None:
        raise PermanentTranslationError("translation provider returned no response (None)")

    items = [str(x) for x in raw]

    if not is_error_response(items):
        cleaned = items
    else:
        cleaned = [filter_error_text(item) for item in items]
        if is_error_response(cleaned):
            raise TransientTranslationError("translation provider returned an error response")

    for original, result in zip(originals, cleaned):
        if not result.strip() and str(original or "").strip():
            raise PermanentTranslationError("translation provider returned an empty response")

    return cleaned

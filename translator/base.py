"""Common interface every translation backend implements, plus the
exception hierarchy used to classify failures as retryable or not."""
from abc import ABC, abstractmethod
import typing as t


class TranslationError(Exception):
    """Base class for every error raised by the translator package."""


class RetryableTranslationError(TranslationError):
    """A transient failure (timeout, 429/5xx, connection error, provider
    error page) worth retrying, possibly against the same engine."""


class NonRetryableTranslationError(TranslationError):
    """A failure that will not be fixed by retrying: bad API key,
    unsupported language, invalid request, missing dependency."""


class EngineUnavailableError(NonRetryableTranslationError):
    """Raised when an engine is selected but isn't usable (missing
    optional dependency, missing required API key, etc.)."""


class TranslationFailedError(TranslationError):
    """Raised when translation could not be completed at all.

    For an explicitly-selected engine (or `Default`, which resolves to a
    concrete engine) this means: the engine failed and, per Rule 1, we do
    NOT silently fall back to another engine -- the job stops here.
    For `Auto`, this means every available engine was exhausted.
    """

    def __init__(self, message: str, engine: t.Optional[str] = None,
                 cause: t.Optional[BaseException] = None) -> None:
        super().__init__(message)
        self.engine = engine
        self.cause = cause


# HTTP statuses (and status-shaped substrings some libraries surface only
# as text) that are worth retrying vs. not. Not every backend can expose a
# real status code -- callers fall back to `classify_exception` below when
# they only have an exception/message to go on.
RETRYABLE_HTTP_STATUSES = {408, 425, 429, 500, 502, 503, 504}
NON_RETRYABLE_HTTP_STATUSES = {400, 401, 402, 403, 404, 406, 422}


def classify_exception(exc: BaseException) -> bool:
    """Best-effort classification of an arbitrary exception as retryable.

    Returns True (retryable) unless the exception/message clearly
    indicates a permanent problem (bad key, unsupported language, missing
    dependency, invalid request). When in doubt, this returns True so the
    existing retry mechanism still gets a chance -- per the task's Rule:
    "if the underlying library does not expose enough information to
    distinguish them safely, use the existing retry mechanism."
    """
    if isinstance(exc, NonRetryableTranslationError):
        return False
    if isinstance(exc, RetryableTranslationError):
        return True

    status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
    if not isinstance(status, int):
        # httpx/requests-style exceptions (e.g. from googletrans, which is
        # httpx-based) put the status code on `exc.response.status_code`
        # rather than directly on the exception -- check there too before
        # falling back to message-text sniffing.
        response = getattr(exc, "response", None)
        status = getattr(response, "status_code", None) if response is not None else None
    if isinstance(status, int):
        if status in NON_RETRYABLE_HTTP_STATUSES:
            return False
        if status in RETRYABLE_HTTP_STATUSES:
            return True

    message = str(exc).lower()
    non_retryable_markers = (
        "invalid api key", "unauthorized", "api key", "apikey",
        "unsupported language", "invalid language",
        "invalid request", "bad request",
        "no module named", "not installed", "missing dependency",
        "invalid configuration",
    )
    if any(marker in message for marker in non_retryable_markers):
        return False

    retryable_markers = (
        "timeout", "timed out", "connection", "temporarily unavailable",
        "too many requests", "rate limit", "429", "500", "502", "503",
        "504", "server error",
    )
    if any(marker in message for marker in retryable_markers):
        return True

    # Unknown shape -- default to retryable rather than giving up early.
    return True


class TranslationBackend(ABC):
    """A single translation provider. Implementations should be cheap to
    construct (lazy-import the underlying SDK inside `__init__`/methods,
    not at module import time) so an unavailable optional dependency never
    prevents the bot from starting -- see `is_available()`."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Stable, snake_case identifier used in config/persistence/Discord
        option values, e.g. "googletrans", "deep_translator", "bing"."""

    @property
    def display_name(self) -> str:
        """Human-friendly name shown in Discord, e.g. "Deep Translator"."""
        return self.name.replace("_", " ").title()

    @property
    def requires_api_key(self) -> bool:
        return False

    def is_available(self) -> bool:
        """Whether this backend can actually be used right now (optional
        dependency importable, required API key present, etc.). The
        default implementation is permissive; backends that need a
        dependency or API key should override this."""
        return True

    @abstractmethod
    async def translate(
            self,
            text: str,
            source_language: str,
            target_language: str,
    ) -> str:
        """Translate a single string. Implementations must validate their
        own result (see `translator.errors`) and raise rather than return
        an error page / empty response as if it were a translation."""

    async def translate_batch(
            self,
            texts: t.List[str],
            source_language: str,
            target_language: str,
    ) -> t.List[str]:
        """Translate a batch of strings. Default implementation just calls
        `translate` for each item; backends with a real batch API (e.g.
        googletrans, deep_translator) should override this for fewer
        round trips."""
        return [
            await self.translate(item, source_language, target_language)
            for item in texts
        ]

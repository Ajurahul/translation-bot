"""LibreTranslate backend.

LibreTranslate (https://libretranslate.com) is an open-source translation
API. Anthropic/this project does not host one -- by default this points
at the public instance, which has a free tier but generally requires an
API key for anything beyond token-level testing. Self-hosted or
community-run LibreTranslate instances are often free and keyless; point
`LIBRETRANSLATE_URL` at one of those if you have it.

Configuration (environment variables):
    LIBRETRANSLATE_URL      Base URL of the instance. Defaults to the
                             public https://libretranslate.com instance.
    LIBRETRANSLATE_API_KEY  Optional. Required by most hosted instances
                             for real usage; not required for self-hosted
                             instances that were started without one.

No credentials are ever hard-coded -- both values are read from the
environment at call time.
"""
import os
import typing as t

from translator.base import (
    NonRetryableTranslationError,
    RetryableTranslationError,
    TranslationBackend,
)
from translator.errors import validate_and_clean

DEFAULT_URL = "https://libretranslate.com"


class LibreTranslateBackend(TranslationBackend):
    name = "libretranslate"
    display_name = "LibreTranslate"

    def __init__(self) -> None:
        # BUG FIX (found in review): the original version opened a brand
        # new `aiohttp.ClientSession()` (i.e. a new connection pool, no
        # keep-alive reuse) on every single translate() call -- exactly
        # the "create client -> destroy" pattern the review flagged.
        # aiohttp explicitly documents ClientSession as safe to share
        # across concurrent coroutines, so one session per backend
        # instance (which itself lives for one translation job -- see
        # TranslationManager._backend_cache) is safe and avoids paying
        # TCP/TLS setup cost for every chunk.
        self._session = None
        self._session_lock = None

    async def _get_session(self):
        import aiohttp
        import asyncio as _asyncio
        if self._session_lock is None:
            self._session_lock = _asyncio.Lock()
        async with self._session_lock:
            if self._session is None or self._session.closed:
                self._session = aiohttp.ClientSession()
            return self._session

    @property
    def requires_api_key(self) -> bool:
        # The API key is optional (self-hosted keyless instances exist),
        # but flagged as "requires" for the public default instance so the
        # admin/setup docs correctly nudge people towards getting one.
        return True

    def is_available(self) -> bool:
        try:
            import aiohttp  # noqa: F401
            return True
        except ImportError:
            return False

    @staticmethod
    def _base_url() -> str:
        return os.getenv("LIBRETRANSLATE_URL", DEFAULT_URL).rstrip("/")

    @staticmethod
    def _api_key() -> t.Optional[str]:
        return os.getenv("LIBRETRANSLATE_API_KEY") or None

    async def _call(self, text: str, source_language: str, target_language: str) -> str:
        import aiohttp

        payload = {
            "q": text,
            "source": source_language if source_language and source_language != "auto" else "auto",
            "target": target_language,
            "format": "text",
        }
        api_key = self._api_key()
        if api_key:
            payload["api_key"] = api_key

        url = f"{self._base_url()}/translate"
        session = await self._get_session()
        try:
            async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status in (401, 403):
                    raise NonRetryableTranslationError(
                        "LibreTranslate rejected the request -- check LIBRETRANSLATE_API_KEY"
                    )
                if resp.status == 429 or resp.status >= 500:
                    raise RetryableTranslationError(f"LibreTranslate returned HTTP {resp.status}")
                if resp.status >= 400:
                    raise NonRetryableTranslationError(f"LibreTranslate returned HTTP {resp.status}")
                data = await resp.json()
        except (NonRetryableTranslationError, RetryableTranslationError):
            raise
        except Exception as e:
            raise RetryableTranslationError(f"LibreTranslate request failed: {e}") from e

        translated = data.get("translatedText")
        if not translated:
            raise RetryableTranslationError("LibreTranslate returned an empty response")
        return str(translated)

    async def aclose(self) -> None:
        """Optional explicit cleanup. Not required for correctness --
        aiohttp will warn-and-close an unclosed session at GC time, which
        is the accepted tradeoff for a backend instance that's cached for
        a whole job with no explicit "job finished" hook to call this
        from -- but callers that do have such a hook (e.g. a future
        TranslationManager.aclose()) can use this to avoid the warning."""
        if self._session is not None and not self._session.closed:
            await self._session.close()

    async def translate(self, text: str, source_language: str, target_language: str) -> str:
        result = await self._call(text, source_language, target_language)
        return validate_and_clean([result])[0]

    async def translate_batch(self, texts: t.List[str], source_language: str, target_language: str) -> t.List[str]:
        import asyncio
        results = await asyncio.gather(*(
            self._call(item, source_language, target_language) for item in texts
        ))
        return validate_and_clean(list(results))

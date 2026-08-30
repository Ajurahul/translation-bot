"""googletrans==4.0.2 backend.

The client is created lazily on first use and then reused for every
subsequent call (instead of the old `async with GoogleTransClient(): ...`
per chunk) -- this alone removes one full client-creation round trip per
chunk, which was one of the biggest contributors to the ~1 hour/2MB
slowdown described in the task.
"""
import asyncio
import typing as t

try:
    from asyncio import Timeout
except ImportError:  # Python < 3.11
    from async_timeout import Timeout

from ..base import ProviderCapabilities, TranslationBackend
from ..errors import PermanentTranslationError, TransientTranslationError

try:
    from googletrans import Translator as _GoogleTransClient

    _AVAILABLE = True
except ImportError:  # pragma: no cover - exercised only when dependency missing
    _GoogleTransClient = None
    _AVAILABLE = False


SERVICE_URLS = [
    "translate.google.com",
    "translate.google.co.in",
    "translate.google.co.kr",
    "translate.google.co.uk",
    "translate.google.ca",
    "translate.google.com.au",
    "translate.google.de",
    "translate.google.fr",
    "translate.google.es",
    "translate.google.it",
    "translate.google.co.jp",
]

# Google's own language codes (used elsewhere in the project) already
# match what googletrans expects, so no override table is needed here.
CALL_TIMEOUT_SECONDS = 10.0


class GoogleTransBackend(TranslationBackend):
    name = "googletrans"
    display_name = "GoogleTrans"
    capabilities = ProviderCapabilities(
        requires_api_key=False, free_without_key=True, supports_batch=True
    )

    def __init__(self, call_timeout: float = CALL_TIMEOUT_SECONDS) -> None:
        self._client = None
        self._lock = asyncio.Lock()
        self._call_timeout = call_timeout

    def is_available(self) -> bool:
        return _AVAILABLE

    async def _get_client(self):
        if not _AVAILABLE:
            raise PermanentTranslationError("googletrans package is not installed")
        if self._client is not None:
            return self._client
        async with self._lock:
            if self._client is None:
                self._client = _GoogleTransClient(
                    timeout=Timeout(15.0),
                    raise_exception=True,
                    service_urls=SERVICE_URLS,
                )
        return self._client

    async def translate(self, text: str, source_language: str, target_language: str) -> str:
        client = await self._get_client()
        try:
            result = await asyncio.wait_for(
                client.translate(str(text), dest=target_language, src=source_language),
                timeout=self._call_timeout,
            )
        except asyncio.TimeoutError as exc:
            raise TransientTranslationError("googletrans timed out") from exc
        except Exception as exc:
            raise TransientTranslationError(str(exc)) from exc
        return str(getattr(result, "text", result))

    async def translate_batch(
        self, texts: t.List[str], source_language: str, target_language: str
    ) -> t.List[str]:
        client = await self._get_client()
        try:
            result = await asyncio.wait_for(
                client.translate(list(texts), dest=target_language, src=source_language),
                timeout=self._call_timeout,
            )
        except asyncio.TimeoutError as exc:
            raise TransientTranslationError("googletrans timed out") from exc
        except Exception as exc:
            raise TransientTranslationError(str(exc)) from exc
        if not isinstance(result, list):
            result = [result]
        return [str(getattr(item, "text", item)) for item in result]

    async def aclose(self) -> None:
        client, self._client = self._client, None
        if client is not None:
            try:
                inner = getattr(client, "client", None)
                if inner is not None and hasattr(inner, "aclose"):
                    await inner.aclose()
            except Exception:
                pass


def register(reg) -> None:
    reg.register("googletrans", GoogleTransBackend, display_name="GoogleTrans")

"""deep-translator==1.11.4 backends.

Only the services that work with no API key/credentials are exposed:
GoogleTranslator and MyMemoryTranslator. deep_translator's client objects
are cheap but not free, and its `.translate()` call is synchronous
(blocking, requests-based) so every call runs on a worker thread via
asyncio.to_thread -- the client itself is cached per (source, target)
pair and reused across calls instead of being rebuilt every time.
"""
import asyncio
import typing as t

from ..base import ProviderCapabilities, TranslationBackend
from ..errors import PermanentTranslationError, TransientTranslationError

try:
    from deep_translator import GoogleTranslator, MyMemoryTranslator

    _AVAILABLE = True
except ImportError:  # pragma: no cover - exercised only when dependency missing
    GoogleTranslator = None
    MyMemoryTranslator = None
    _AVAILABLE = False

CALL_TIMEOUT_SECONDS = 15.0
BATCH_TIMEOUT_SECONDS = 30.0


class _DeepTranslatorBackend(TranslationBackend):
    """Shared implementation; subclasses just set `_client_cls`."""

    _client_cls: t.ClassVar[t.Any] = None
    capabilities = ProviderCapabilities(
        requires_api_key=False, free_without_key=True, supports_batch=True
    )

    def __init__(self) -> None:
        self._clients: t.Dict[t.Tuple[str, str], t.Any] = {}
        self._lock = asyncio.Lock()

    def is_available(self) -> bool:
        return _AVAILABLE and self._client_cls is not None

    async def _get_client(self, source_language: str, target_language: str):
        if not self.is_available():
            raise PermanentTranslationError(f"{self.name} package is not installed")
        key = (source_language or "auto", target_language)
        client = self._clients.get(key)
        if client is not None:
            return client
        async with self._lock:
            client = self._clients.get(key)
            if client is None:
                client = self._client_cls(source=key[0], target=key[1])
                self._clients[key] = client
        return client

    async def translate(self, text: str, source_language: str, target_language: str) -> str:
        client = await self._get_client(source_language, target_language)

        def _work() -> str:
            return str(client.translate(str(text)))

        try:
            return await asyncio.wait_for(asyncio.to_thread(_work), timeout=CALL_TIMEOUT_SECONDS)
        except asyncio.TimeoutError as exc:
            raise TransientTranslationError(f"{self.name} timed out") from exc
        except Exception as exc:
            raise TransientTranslationError(str(exc)) from exc

    async def translate_batch(
        self, texts: t.List[str], source_language: str, target_language: str
    ) -> t.List[str]:
        client = await self._get_client(source_language, target_language)

        def _work() -> t.List[str]:
            if hasattr(client, "translate_batch"):
                return [str(item) for item in client.translate_batch(list(texts))]
            return [str(client.translate(str(item))) for item in texts]

        try:
            return await asyncio.wait_for(asyncio.to_thread(_work), timeout=BATCH_TIMEOUT_SECONDS)
        except asyncio.TimeoutError as exc:
            raise TransientTranslationError(f"{self.name} timed out") from exc
        except Exception as exc:
            raise TransientTranslationError(str(exc)) from exc


class DeepGoogleBackend(_DeepTranslatorBackend):
    name = "deep-google"
    display_name = "Deep Translator - Google"
    _client_cls = GoogleTranslator


class DeepMyMemoryBackend(_DeepTranslatorBackend):
    name = "deep-mymemory"
    display_name = "Deep Translator - MyMemory"
    _client_cls = MyMemoryTranslator


def register(reg) -> None:
    reg.register("deep-google", DeepGoogleBackend, display_name="Deep Translator - Google")
    reg.register("deep-mymemory", DeepMyMemoryBackend, display_name="Deep Translator - MyMemory")

"""deep-translator==1.11.4 backends.

The no-key services are exposed here: GoogleTranslator and
MyMemoryTranslator. deep_translator's `.translate()` call is synchronous
(blocking, plain `requests.get()` per call -- it holds no persistent
session or other event-loop-bound resource, verified against the
installed package source), so it's safe to reuse a cached client object
across different event loops/threads; every call still runs on a worker
thread via asyncio.to_thread since it's blocking I/O.

The keyed engines (DeepL, Microsoft, Papago, Baidu) live in
deep_translator_keyed_backend.py, which builds on `_DeepTranslatorBackend`
here.
"""
import asyncio
import threading
import typing as t

from ..base import ProviderCapabilities, TranslationBackend
from ..errors import PermanentTranslationError, TransientTranslationError, is_mymemory_quota_warning
from . import language_map
from .text_chunking import split_text

try:
    from deep_translator import GoogleTranslator, MyMemoryTranslator

    _AVAILABLE = True
except ImportError:  # pragma: no cover - exercised only when dependency missing
    GoogleTranslator = None
    MyMemoryTranslator = None
    _AVAILABLE = False

try:
    from deep_translator.constants import MY_MEMORY_LANGUAGES_TO_CODES
except ImportError:  # pragma: no cover - exercised only when dependency missing
    MY_MEMORY_LANGUAGES_TO_CODES = {}

CALL_TIMEOUT_SECONDS = 15.0
BATCH_TIMEOUT_SECONDS = 30.0

# MyMemory rejects any single request over ~500 characters; stay under
# that with a safety margin rather than hugging the exact limit.
MYMEMORY_CHUNK_LIMIT = 480


class _DeepTranslatorBackend(TranslationBackend):
    """Shared implementation; subclasses just set `_client_cls`."""

    _client_cls: t.ClassVar[t.Any] = None
    capabilities = ProviderCapabilities(
        requires_api_key=False, free_without_key=True, supports_batch=True
    )

    def __init__(self) -> None:
        self._clients: t.Dict[t.Tuple[str, str], t.Any] = {}
        # threading.Lock, not asyncio.Lock: this backend instance is a
        # process-wide singleton (translation/registry.py) called from
        # many different OS threads, each running its own independent
        # event loop -- see translation/providers/googletrans_backend.py's
        # module docstring for why asyncio.Lock is unsafe across threads
        # like that (two threads could race past it and each construct
        # a client for the same key, one silently overwriting the other
        # in self._clients).
        self._lock = threading.Lock()

    def is_available(self) -> bool:
        return _AVAILABLE and self._client_cls is not None

    def _get_client(self, source_language: str, target_language: str):
        if not self.is_available():
            raise PermanentTranslationError(f"{self.name} package is not installed")
        key = (source_language or "auto", target_language)
        client = self._clients.get(key)
        if client is not None:
            return client
        with self._lock:
            client = self._clients.get(key)
            if client is None:
                client = self._client_cls(source=key[0], target=key[1])
                self._clients[key] = client
        return client

    async def translate(self, text: str, source_language: str, target_language: str) -> str:
        client = self._get_client(source_language, target_language)

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
        client = self._get_client(source_language, target_language)

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
    """MyMemory via deep-translator, with three quirks handled that the
    generic `_DeepTranslatorBackend` doesn't account for:

    1. It rejects any single request over ~500 characters -- long text
       is split into <=MYMEMORY_CHUNK_LIMIT-character chunks on the
       nearest paragraph/sentence/word boundary (never mid-word, except
       as an unavoidable last resort -- see text_chunking.py) and
       rejoined.
    2. It expects region-tagged codes ("en-GB", "ko-KR", ...) rather
       than this bot's plain 2-letter codes -- mapped via the shared
       `language_map` helper against deep_translator's
       MY_MEMORY_LANGUAGES_TO_CODES table.
    3. On exceeded free quota it doesn't raise an HTTP error -- it
       returns a warning string ("MYMEMORY WARNING", "YOU USED ALL
       AVAILABLE FREE TRANSLATIONS", "QUERY LENGTH LIMIT EXCEEDED", ...)
       *as if it were the translation*. Every chunk's result is checked
       for this before being accepted, and treated as a real failure
       (TransientTranslationError) if found.
    """

    name = "deep-mymemory"
    display_name = "Deep Translator - MyMemory"
    _client_cls = MyMemoryTranslator

    def _map_code(self, code: str) -> str:
        return language_map.map_language_code(
            self.name, code, lambda: MY_MEMORY_LANGUAGES_TO_CODES
        )

    def _get_client(self, source_language: str, target_language: str):
        return super()._get_client(
            self._map_code(source_language), self._map_code(target_language)
        )

    @staticmethod
    def _reject_if_quota_warning(result: str) -> str:
        if is_mymemory_quota_warning(result):
            raise TransientTranslationError(
                f"MyMemory quota/limit warning: {str(result)[:200]}"
            )
        return result

    async def translate(self, text: str, source_language: str, target_language: str) -> str:
        client = self._get_client(source_language, target_language)
        pieces = split_text(str(text), MYMEMORY_CHUNK_LIMIT) or [str(text)]

        def _work() -> str:
            translated = [
                self._reject_if_quota_warning(str(client.translate(piece)))
                for piece in pieces
                if piece
            ]
            return "".join(translated)

        try:
            timeout = CALL_TIMEOUT_SECONDS * max(1, len(pieces))
            return await asyncio.wait_for(asyncio.to_thread(_work), timeout=timeout)
        except asyncio.TimeoutError as exc:
            raise TransientTranslationError(f"{self.name} timed out") from exc
        except TransientTranslationError:
            raise
        except Exception as exc:
            raise TransientTranslationError(str(exc)) from exc

    async def translate_batch(
        self, texts: t.List[str], source_language: str, target_language: str
    ) -> t.List[str]:
        # MyMemory has no real batch endpoint in deep-translator -- each
        # item goes through translate() individually so it gets the same
        # per-item chunking and quota-warning check as a lone call.
        results = []
        for item in texts:
            results.append(await self.translate(item, source_language, target_language))
        return results


def register(reg) -> None:
    reg.register("deep-google", DeepGoogleBackend, display_name="Deep Translator - Google")
    reg.register("deep-mymemory", DeepMyMemoryBackend, display_name="Deep Translator - MyMemory")

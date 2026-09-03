"""googletrans==4.0.2 backend.

The client is created lazily on first use and then reused -- instead of
the old `async with GoogleTransClient(): ...` per chunk -- which removes
one full client-creation round trip per chunk.

Important constraint this backend has to respect: googletrans' client
holds an `httpx.AsyncClient`, whose internal connection-pool primitives
bind to whichever event loop is *running* the first time they're
actually used -- not to the loop active at construction time. This
project processes each chunk via `utils.translate.Translator.
_run_async_blocking()`, which calls `asyncio.run(...)` fresh per chunk
(see that module) -- so a naive "cache forever, reuse across every call"
client would, after the very first chunk, start reusing pool internals
bound to an event loop that `asyncio.run()` has already closed. In
practice that surfaces as every call after the first raising something
like "Future/Lock attached to a different loop" (a well-known httpx/
anyio failure mode), which would make googletrans fail for every chunk
past the first one processed by a given worker thread.

`_get_client()` below tracks which running loop the cached client was
last used on and transparently rebuilds the client whenever that loop
has changed, so reuse still happens *within* one loop/call (still avoids
rebuilding the client for concurrent requests inside a single async
call) without ever reusing loop-bound internals across a closed loop.
"""
import asyncio
import typing as t
import threading
import inspect

import httpx

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



    async def _call_translate(self, client, text, src, dest):
        result = client.translate(text, src=src, dest=dest)

        if inspect.isawaitable(result):
            return await asyncio.wait_for(
                result,
                timeout=self._call_timeout,
            )

        return result

    def __init__(self, call_timeout: float = CALL_TIMEOUT_SECONDS) -> None:
        self._client = None
        self._client_loop = None
        # A plain threading.Lock, not asyncio.Lock: this backend instance
        # is a process-wide singleton (see translation/registry.py) that
        # gets called concurrently from *different OS threads*, each
        # running its own independent event loop (see the module
        # docstring). asyncio.Lock is not safe to use across genuinely
        # different threads/loops -- two threads could both see
        # "no client yet" at once and race to construct/overwrite one,
        # leaking the loser's httpx client. threading.Lock has no such
        # restriction; the critical section here is plain, fast,
        # synchronous object construction, so briefly blocking one
        # thread's loop for it is negligible.
        self._lock = threading.Lock()
        self._call_timeout = call_timeout

    def is_available(self) -> bool:
        return _AVAILABLE

    async def _get_client(self):
        if not _AVAILABLE:
            raise PermanentTranslationError("googletrans package is not installed")

        loop = asyncio.get_running_loop()
        if self._client is not None and self._client_loop is loop:
            return self._client

        with self._lock:
            if self._client is None or self._client_loop is not loop:

                self._client = _GoogleTransClient(
                    timeout=httpx.Timeout(15.0),
                    raise_exception=True,
                    service_urls=SERVICE_URLS,
                )
                self._client_loop = loop
        return self._client

    async def translate(self, text: str, source_language: str, target_language: str) -> str:
        client = await self._get_client()
        try:
            result = await self._call_translate(
                client,
                str(text),
                source_language,
                target_language,
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
            result = await self._call_translate(
                client,
                list(texts),
                source_language,
                target_language,
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
        self._client_loop = None
        if client is not None:
            try:
                inner = getattr(client, "client", None)
                if inner is not None and hasattr(inner, "aclose"):
                    await inner.aclose()
            except Exception:
                pass


def register(reg) -> None:
    reg.register("googletrans", GoogleTransBackend, display_name="GoogleTrans")

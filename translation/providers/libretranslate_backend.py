"""LibreTranslate (https://libretranslate.com / self-hostable).

Implemented as a direct REST call rather than through deep_translator's
LibreTranslator wrapper: that wrapper (as shipped in deep-translator
1.11.4) unconditionally raises unless an api_key is supplied, even
against mirrors that don't actually require one -- verified against the
installed package source. Calling the API directly avoids that false
requirement.

Uses a public community mirror by default (no key needed there today).
Configurable via environment variables so an operator can point this at
their own self-hosted instance or a mirror that does require a key:

    LIBRETRANSLATE_URL      - base URL (default: https://translate.argosopentech.com)
    LIBRETRANSLATE_API_KEY  - optional; sent only if set

Like the other community-mirror-backed providers here, this is an
unofficial dependency on a third party's uptime/policy and can change
without notice.
"""
import os
import typing as t

from ..base import ProviderCapabilities, TranslationBackend
from ..errors import PermanentTranslationError, TransientTranslationError
from .http_backend import HttpJsonBackend

DEFAULT_BASE_URL = "https://translate.argosopentech.com"


class LibreTranslateBackend(HttpJsonBackend, TranslationBackend):
    name = "libretranslate"
    display_name = "LibreTranslate"
    capabilities = ProviderCapabilities(
        requires_api_key=False, free_without_key=True, supports_batch=False
    )

    def is_available(self) -> bool:
        return self.httpx_available()

    def _base_url(self) -> str:
        return os.getenv("LIBRETRANSLATE_URL", DEFAULT_BASE_URL).rstrip("/")

    async def translate(self, text: str, source_language: str, target_language: str) -> str:
        payload: t.Dict[str, t.Any] = {
            "q": str(text),
            "source": source_language or "auto",
            "target": target_language,
            "format": "text",
        }
        api_key = os.getenv("LIBRETRANSLATE_API_KEY")
        if api_key:
            payload["api_key"] = api_key

        data = await self._request_json("POST", f"{self._base_url()}/translate", json_body=payload)
        if not isinstance(data, dict):
            raise TransientTranslationError("LibreTranslate returned an unexpected response")
        if "error" in data:
            raise TransientTranslationError(f"LibreTranslate error: {data['error']}")
        translated = data.get("translatedText")
        if translated is None:
            raise PermanentTranslationError("LibreTranslate response missing translatedText")
        return str(translated)


def register(reg) -> None:
    reg.register("libretranslate", LibreTranslateBackend, display_name="LibreTranslate")

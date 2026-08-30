"""Lingva Translate (https://github.com/thedaviddelta/lingva-translate).

A free, open-source, no-API-key front end for Google Translate with
several public community-run instances. No API key, no paid tier -- it's
a plain GET request returning JSON. Configurable base URL via the
LINGVA_URL environment variable if the default public instance becomes
unavailable or rate-limits; otherwise this "just works" with zero setup,
which is why it's included alongside the translators-package providers.

Like the translators-package providers, this is an unofficial/community
endpoint and can change or go down without notice -- there's no SLA.
"""
import os
import urllib.parse

from ..base import ProviderCapabilities, TranslationBackend
from .http_backend import HttpJsonBackend

DEFAULT_BASE_URL = "https://lingva.ml/api/v1"


class LingvaBackend(HttpJsonBackend, TranslationBackend):
    name = "lingva"
    display_name = "Lingva Translate"
    capabilities = ProviderCapabilities(
        requires_api_key=False, free_without_key=True, supports_batch=False
    )

    def is_available(self) -> bool:
        return self.httpx_available()

    def _base_url(self) -> str:
        return os.getenv("LINGVA_URL", DEFAULT_BASE_URL).rstrip("/")

    async def translate(self, text: str, source_language: str, target_language: str) -> str:
        source = source_language or "auto"
        encoded_text = urllib.parse.quote(str(text), safe="")
        url = f"{self._base_url()}/{source}/{target_language}/{encoded_text}"
        data = await self._request_json("GET", url)
        translation = data.get("translation") if isinstance(data, dict) else None
        return str(translation) if translation is not None else ""


def register(reg) -> None:
    reg.register("lingva", LingvaBackend, display_name="Lingva Translate")

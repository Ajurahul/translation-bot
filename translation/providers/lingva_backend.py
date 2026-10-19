"""Lingva Translate (https://github.com/thedaviddelta/lingva-translate).

A free, open-source, no-API-key front end for Google Translate with
several public community-run instances. No API key, no paid tier -- it's
a plain GET request returning JSON. Configurable base URL via the
LINGVA_URL environment variable if the default public instance becomes
unavailable or rate-limits; otherwise this "just works" with zero setup,
which is why it's included alongside the translators-package providers.

Like the translators-package providers, this is an unofficial/community
endpoint and can change or go down without notice -- there's no SLA.
(The project's own official instance, lingva.ml, has itself been
unreliable -- Cloudflare 523s and, more recently, bot-abuse lockdowns
requiring an API key on the public instance. lingva.lunar.icu is used
as the default here instead, per the project's own current instance
list at github.com/thedaviddelta/lingva-translate/blob/main/instances.json
-- but community instances rotate; if this one goes down too, set
LINGVA_URL to a current one from that list, or self-host.)
"""
import os
import typing as t
import urllib.parse

from ..base import ProviderCapabilities, TranslationBackend
from ..errors import PermanentTranslationError, TransientTranslationError
from .http_backend import HttpJsonBackend, split_urls

DEFAULT_BASE_URL = "https://lingva.lunar.icu/api/v1"


class LingvaBackend(HttpJsonBackend, TranslationBackend):
    name = "lingva"
    display_name = "Lingva Translate"
    capabilities = ProviderCapabilities(
        requires_api_key=False, free_without_key=True, supports_batch=False
    )

    def is_available(self) -> bool:
        return self.httpx_available()

    def _base_url(self) -> str:
        return self._base_urls()[0]

    def _base_urls(self) -> t.List[str]:
        # LINGVA_URL may list several instances, comma-separated; they are
        # tried in order and the first that answers wins.
        return split_urls(os.getenv("LINGVA_URL"), DEFAULT_BASE_URL)

    async def translate(self, text: str, source_language: str, target_language: str) -> str:
        source = source_language or "auto"
        encoded_text = urllib.parse.quote(str(text), safe="")
        last_error: t.Optional[Exception] = None
        for base in self._base_urls():
            url = f"{base}/{source}/{target_language}/{encoded_text}"
            try:
                data = await self._request_json("GET", url)
            except TransientTranslationError as exc:
                last_error = exc  # this instance is down/blocked: try the next
                continue
            translation = data.get("translation") if isinstance(data, dict) else None
            if translation is None:
                # Never hand back "" for a real input: it would be rejected
                # later anyway, and this way the next instance gets a chance.
                last_error = TransientTranslationError("Lingva response missing 'translation'")
                continue
            return str(translation)
        raise last_error or PermanentTranslationError("no Lingva instance configured")


def register(reg) -> None:
    reg.register("lingva", LingvaBackend, display_name="Lingva Translate")

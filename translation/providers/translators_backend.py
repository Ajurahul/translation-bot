"""Backends built on the `translators` package.

`translators` exposes dozens of services, but most require paid
credentials or are unreliable for bulk use. Only free, no-API-key
services are registered here (each works by talking to the provider's
public web-translate endpoint the same way a browser would -- there's no
official paid API involved, but that also means these are unofficial
endpoints that can change or rate-limit without notice; see
docs/TRANSLATION_ENGINES.md for the full classification of every
provider considered).

None of these backends support a real batch call in `translators`, so
the default sequential translate_batch() from TranslationBackend is used.
"""
import asyncio
import os
import typing as t

from ..base import ProviderCapabilities, TranslationBackend
from ..errors import PermanentTranslationError, TransientTranslationError

try:
    # `translators` probes the network at import time to pick a server
    # region (translators.server.TranslatorsServer.__init__ calls out to
    # an IP-geolocation endpoint) and raises TranslatorError if that
    # probe fails -- which would otherwise crash bot startup any time
    # outbound network access is restricted or flaky. Pre-setting the
    # region env var (as the package's own error message recommends)
    # skips that network call entirely.
    os.environ.setdefault("translators_default_region", "EN")
    import translators as _translators

    _AVAILABLE = True
except Exception:  # pragma: no cover - exercised only when dependency missing/unreachable
    _translators = None
    _AVAILABLE = False

CALL_TIMEOUT_SECONDS = 15.0

# Only Bing needs an override table today (its region-variant codes for
# Chinese, plus a couple of legacy Google codes it doesn't recognize).
# Anything not listed passes through unchanged.
_LANGUAGE_OVERRIDES: t.Dict[str, t.Dict[str, str]] = {
    "bing": {
        "zh-cn": "zh-Hans",
        "zh-tw": "zh-Hant",
        "iw": "he",
        "tl": "fil",
    },
}


class _TranslatorsPackageBackend(TranslationBackend):
    """Shared implementation; subclasses set `provider_key` to the id
    `translators.translate_text(translator=...)` expects."""

    provider_key: str = ""
    capabilities = ProviderCapabilities(
        requires_api_key=False, free_without_key=True, supports_batch=False
    )
    required_env: t.Optional[str] = None

    def is_available(self) -> bool:
        if not _AVAILABLE:
            return False
        if self.required_env and not os.getenv(self.required_env):
            return False
        return True

    def _code(self, value: str) -> str:
        overrides = _LANGUAGE_OVERRIDES.get(self.provider_key, {})
        if not value or value == "auto":
            return "auto"
        return overrides.get(value.lower(), value)

    async def translate(self, text: str, source_language: str, target_language: str) -> str:
        if not self.is_available():
            raise PermanentTranslationError(f"{self.name} is not available")

        from_code = self._code(source_language)
        to_code = self._code(target_language)

        def _work() -> str:
            return str(
                _translators.translate_text(
                    str(text),
                    translator=self.provider_key,
                    from_language=from_code,
                    to_language=to_code,
                )
            )

        try:
            return await asyncio.wait_for(asyncio.to_thread(_work), timeout=CALL_TIMEOUT_SECONDS)
        except asyncio.TimeoutError as exc:
            raise TransientTranslationError(f"{self.name} timed out") from exc
        except Exception as exc:
            raise TransientTranslationError(str(exc)) from exc


class TranslatorsGoogleBackend(_TranslatorsPackageBackend):
    name = "translators-google"
    display_name = "Translators - Google"
    provider_key = "google"


class TranslatorsBingBackend(_TranslatorsPackageBackend):
    name = "translators-bing"
    display_name = "Translators - Bing"
    provider_key = "bing"


class TranslatorsMyMemoryBackend(_TranslatorsPackageBackend):
    name = "translators-mymemory"
    display_name = "Translators - MyMemory"
    provider_key = "myMemory"


class TranslatorsYandexBackend(_TranslatorsPackageBackend):
    name = "translators-yandex"
    display_name = "Translators - Yandex"
    provider_key = "yandex"


class TranslatorsApertiumBackend(_TranslatorsPackageBackend):
    name = "translators-apertium"
    display_name = "Translators - Apertium"
    provider_key = "apertium"


class TranslatorsReversoBackend(_TranslatorsPackageBackend):
    name = "translators-reverso"
    display_name = "Translators - Reverso"
    provider_key = "reverso"


def register(reg) -> None:
    reg.register("translators-google", TranslatorsGoogleBackend, display_name="Translators - Google")
    reg.register("translators-bing", TranslatorsBingBackend, display_name="Translators - Bing")
    reg.register(
        "translators-mymemory", TranslatorsMyMemoryBackend, display_name="Translators - MyMemory"
    )
    reg.register("translators-yandex", TranslatorsYandexBackend, display_name="Translators - Yandex")
    reg.register(
        "translators-apertium", TranslatorsApertiumBackend, display_name="Translators - Apertium"
    )
    reg.register(
        "translators-reverso", TranslatorsReversoBackend, display_name="Translators - Reverso"
    )

"""Common interface implemented by every translation provider.

The rest of the application (TranslationManager, the Discord cogs) never
imports googletrans / deep_translator / translators directly -- it only
ever talks to a `TranslationBackend`. This is what makes it possible to
add a new provider without touching the manager, the registry lookups, or
the Discord command layer.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
import typing as t


@dataclass(frozen=True)
class ProviderCapabilities:
    """What a provider can/needs. Informational -- used by the registry's
    availability checks and by documentation, not enforced by the manager."""
    requires_api_key: bool = False
    free_without_key: bool = True
    supports_batch: bool = False
    supports_auto_source: bool = True


class TranslationBackend(ABC):
    """One selectable translation provider (e.g. "googletrans",
    "deep-google", "translators-bing"). Instances are long-lived -- the
    registry creates one instance per provider and reuses it for the life
    of the process, so a backend that holds an HTTP client should create
    it lazily on first use and keep reusing it rather than recreating it
    per call."""

    name: str = ""
    display_name: str = ""
    capabilities: ProviderCapabilities = ProviderCapabilities()

    @abstractmethod
    async def translate(
        self,
        text: str,
        source_language: str,
        target_language: str,
    ) -> str:
        """Translate a single string. Raise translation.errors.
        TransientTranslationError for retry-worthy failures (timeouts,
        429/500/502/503/504, error pages) and PermanentTranslationError
        for anything else (missing dependency, bad config)."""
        raise NotImplementedError

    async def translate_batch(
        self,
        texts: t.List[str],
        source_language: str,
        target_language: str,
    ) -> t.List[str]:
        """Translate several strings. Providers that support a real batch
        API should override this; the default falls back to sequential
        single-item calls so every backend works without extra code."""
        return [
            await self.translate(text, source_language, target_language)
            for text in texts
        ]

    def is_available(self) -> bool:
        """Whether this provider can currently be used: dependency
        installed + any required credentials present. Must never raise."""
        return True

    async def aclose(self) -> None:
        """Release any held resources (HTTP clients, etc). Best-effort."""
        return None

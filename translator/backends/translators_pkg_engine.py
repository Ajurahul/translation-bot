"""Generic backend for additional free providers exposed by the
`translators` package (https://pypi.org/project/translators/).

The rest of the application never talks to `translators` directly --
everything goes through this one wrapper (section 20's requirement).

Only providers that are usable *without any payment or mandatory API key*
are wired up here (see translator/registry.py for the curated list and
why bing/deepl/etc. are or aren't included). `translators` exposes ~35
providers; most either require a paid key, are effectively deprecated, or
are not reliable enough for bulk text -- we deliberately do not expose
every provider in the pool just because the package supports it.

Important operational note: on first import, `translators` tries to
auto-detect your server's region over the network, and if that lookup
fails it falls back to an interactive `input()` prompt -- which hangs
forever in a headless/Docker environment with no TTY. Setting the
`translators_default_region` env var (done in `is_available()` below,
before the import) skips that detection entirely and avoids the hang.
"""
import asyncio
import os
import typing as t

from translator.base import TranslationBackend
from translator.errors import validate_and_clean


def _ensure_region_configured() -> None:
    # "EN" avoids both the network geolocation call and the interactive
    # prompt if that call fails; it does not restrict which providers are
    # reachable, it only picks which server pool `translators` prefers.
    os.environ.setdefault("translators_default_region", "EN")


class TranslatorsPackageBackend(TranslationBackend):
    """A single provider from the `translators` package, e.g. "baidu"."""

    def __init__(self, provider: str, display_name: t.Optional[str] = None) -> None:
        self._provider = provider
        self._display_name = display_name or provider.title()

    @property
    def name(self) -> str:
        return self._provider

    @property
    def display_name(self) -> str:
        return self._display_name

    def is_available(self) -> bool:
        _ensure_region_configured()
        try:
            import translators  # noqa: F401
            return True
        except ImportError:
            return False

    @staticmethod
    def _normalize_code(code: str) -> str:
        if not code or code == "auto":
            return "auto"
        return code

    async def _call(self, text: str, source_language: str, target_language: str) -> str:
        _ensure_region_configured()

        def _work() -> str:
            import translators as ts
            return str(ts.translate_text(
                str(text),
                translator=self._provider,
                from_language=self._normalize_code(source_language),
                to_language=self._normalize_code(target_language),
            ))

        return await asyncio.to_thread(_work)

    async def translate(self, text: str, source_language: str, target_language: str) -> str:
        result = await self._call(text, source_language, target_language)
        return validate_and_clean([result])[0]

    async def translate_batch(self, texts: t.List[str], source_language: str, target_language: str) -> t.List[str]:
        # The `translators` package has no first-class batch endpoint for
        # most of these providers, so we translate items concurrently
        # (bounded by the caller's semaphore) rather than serially.
        results = await asyncio.gather(*(
            self._call(item, source_language, target_language) for item in texts
        ))
        return validate_and_clean(list(results))

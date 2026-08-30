"""Central registry of every translation engine the bot knows about.

Adding a new engine means adding one `EngineSpec` here plus (if it's not
a `translators`-package provider) one small backend class in
translator/backends/ -- nothing else needs to change.
"""
from dataclasses import dataclass
import typing as t

from translator.base import TranslationBackend
from translator.backends.bing_engine import BingBackend
from translator.backends.deep_translator_engine import DeepTranslatorBackend
from translator.backends.googletrans_engine import GoogleTransBackend
from translator.backends.libretranslate_engine import LibreTranslateBackend
from translator.backends.translators_pkg_engine import TranslatorsPackageBackend


@dataclass(frozen=True)
class EngineSpec:
    key: str                       # stable id: config value, Discord option value
    display_name: str              # shown in Discord / admin messages
    factory: t.Callable[[], TranslationBackend]
    api_key_tier: str              # "none" | "free_tier" | "paid"
    api_key_env: t.Optional[str] = None
    notes: str = ""

    def build(self) -> TranslationBackend:
        return self.factory()


# --- No API key required -------------------------------------------------
# Either uses a library that talks to a public, keyless endpoint
# (googletrans, deep_translator), or a `translators`-package provider that
# is reverse-engineered/scrape-based and does not require any credentials.
# These can all be rate-limited or temporarily blocked by the provider --
# that's exactly what Auto mode's failure tracking exists for.

_NO_KEY_SPECS: t.List[EngineSpec] = [
    EngineSpec(
        key="googletrans",
        display_name="GoogleTrans",
        factory=GoogleTransBackend,
        api_key_tier="none",
        notes="Unofficial Google Translate client library. No account or key needed.",
    ),
    EngineSpec(
        key="deep_translator",
        display_name="Deep Translator",
        factory=DeepTranslatorBackend,
        api_key_tier="none",
        notes="deep-translator's GoogleTranslator backend. No account or key needed.",
    ),
    EngineSpec(
        key="bing",
        display_name="Bing",
        factory=BingBackend,
        api_key_tier="none",
        notes="Via the `translators` package's bing provider. No account or key needed.",
    ),
    EngineSpec(
        key="baidu",
        display_name="Baidu",
        factory=lambda: TranslatorsPackageBackend("baidu", "Baidu"),
        api_key_tier="none",
        notes="Via the `translators` package. No account or key needed. "
              "Best for Chinese<->other-language pairs.",
    ),
    EngineSpec(
        key="alibaba",
        display_name="Alibaba",
        factory=lambda: TranslatorsPackageBackend("alibaba", "Alibaba"),
        api_key_tier="none",
        notes="Via the `translators` package. No account or key needed.",
    ),
    EngineSpec(
        key="youdao",
        display_name="Youdao",
        factory=lambda: TranslatorsPackageBackend("youdao", "Youdao"),
        api_key_tier="none",
        notes="Via the `translators` package. No account or key needed. "
              "Best for Chinese<->other-language pairs.",
    ),
    EngineSpec(
        key="mymemory",
        display_name="MyMemory",
        factory=lambda: TranslatorsPackageBackend("myMemory", "MyMemory"),
        api_key_tier="none",
        notes="Free translation-memory API. Anonymous usage is capped at "
              "~5,000 words/day; see docs for how to raise that for free.",
    ),
]

# --- Free API tier, but a key is required/recommended ---------------------

_FREE_KEY_SPECS: t.List[EngineSpec] = [
    EngineSpec(
        key="libretranslate",
        display_name="LibreTranslate",
        factory=LibreTranslateBackend,
        api_key_tier="free_tier",
        api_key_env="LIBRETRANSLATE_API_KEY",
        notes="Open-source translation API. The public instance generally "
              "requires a free API key for real usage; self-hosted/"
              "community instances (set LIBRETRANSLATE_URL) may not.",
    ),
]

ALL_SPECS: t.List[EngineSpec] = [*_NO_KEY_SPECS, *_FREE_KEY_SPECS]
_SPECS_BY_KEY: t.Dict[str, EngineSpec] = {spec.key: spec for spec in ALL_SPECS}

# Explicitly NOT supported: any `translators`-package provider that is
# paid-only, enterprise-only, or requires a mandatory paid API key --
# e.g. deepl, sysTran, languageWire, cloudTranslation, lara, modernMt,
# caiyun, lingvanex, niutrans. These appear in the package's provider pool
# but a working Python package does not, by itself, mean the service is
# free (per Rule 9 / section 3) -- so they're deliberately left out.


def get_spec(key: str) -> t.Optional[EngineSpec]:
    return _SPECS_BY_KEY.get(key)


def all_specs() -> t.List[EngineSpec]:
    return list(ALL_SPECS)


_availability_cache: t.Dict[str, bool] = {}


def is_engine_available(key: str, use_cache: bool = True) -> bool:
    """Whether an engine's optional dependency/config is present.

    Availability is cheap to check (import + presence of an env var) but
    still worth caching for the lifetime of the process -- it's checked
    every time the /translate and /set_translation_engine option lists are
    built (i.e. on every autocomplete keystroke)."""
    if use_cache and key in _availability_cache:
        return _availability_cache[key]
    spec = get_spec(key)
    if spec is None:
        return False
    try:
        available = spec.build().is_available()
    except Exception:
        available = False
    _availability_cache[key] = available
    return available


def reset_availability_cache() -> None:
    _availability_cache.clear()


def available_specs() -> t.List[EngineSpec]:
    return [spec for spec in ALL_SPECS if is_engine_available(spec.key)]


def get_backend(key: str) -> TranslationBackend:
    spec = get_spec(key)
    if spec is None:
        raise KeyError(f"Unknown translation engine: {key!r}")
    return spec.build()

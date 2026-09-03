"""Optional, free-with-signup deep-translator engines: DeepL, Microsoft
Translator (Azure), Naver Papago, and Baidu Translate.

Each is only ever added to the engine rotation if its required API
key/credential environment variable(s) are actually set -- `is_available()`
returns False otherwise, exactly like every other keyed provider in this
project (see ai_backend.py), so a deployment that hasn't configured one
just never sees it as a candidate: no partial/broken entries, no silent
failures on startup.

Env vars:
    DEEPL_API_KEY                          - enables "deepl"
    MICROSOFT_API_KEY, MICROSOFT_REGION    - enable "microsoft"
                                              (region optional but recommended)
    PAPAGO_CLIENT_ID, PAPAGO_SECRET_KEY     - both required to enable "papago"
    BAIDU_APP_ID, BAIDU_APP_KEY             - both required to enable "baidu"

All four share one generic language-code-mapping helper
(translation/providers/language_map.py) rather than four bespoke ones --
see that module for the algorithm.
"""
import asyncio
import os
import typing as t

from ..errors import PermanentTranslationError, TransientTranslationError
from . import language_map
from .deep_translator_backend import CALL_TIMEOUT_SECONDS, BATCH_TIMEOUT_SECONDS, _DeepTranslatorBackend

try:
    from deep_translator import BaiduTranslator, DeeplTranslator, MicrosoftTranslator, PapagoTranslator

    _AVAILABLE = True
except ImportError:  # pragma: no cover - exercised only when dependency missing
    BaiduTranslator = None
    DeeplTranslator = None
    MicrosoftTranslator = None
    PapagoTranslator = None
    _AVAILABLE = False

try:
    from deep_translator.constants import BAIDU_LANGUAGE_TO_CODE, DEEPL_LANGUAGE_TO_CODE, PAPAGO_LANGUAGE_TO_CODE
except ImportError:  # pragma: no cover - exercised only when dependency missing
    BAIDU_LANGUAGE_TO_CODE = {}
    DEEPL_LANGUAGE_TO_CODE = {}
    PAPAGO_LANGUAGE_TO_CODE = {}


class _KeyedDeepTranslatorBackend(_DeepTranslatorBackend):
    """Shared base for engines that need credentials to even construct a
    client. Subclasses set `required_env` (checked by `is_available()`)
    and implement `_construct_client()` / `_lang_dict()`.

    Unlike `_DeepTranslatorBackend._get_client`, client construction here
    happens inside the worker thread (see translate()/translate_batch()
    below) rather than directly on the event loop -- Microsoft's client
    fetches its supported-language list over the network during
    `__init__`, which would otherwise block the event loop on first use.
    """

    required_env: t.ClassVar[t.Tuple[str, ...]] = ()

    def is_available(self) -> bool:
        return _AVAILABLE and all(os.getenv(var) for var in self.required_env)

    def _lang_dict(self) -> t.Dict[str, str]:  # pragma: no cover - overridden
        return {}

    def _construct_client(self, source: str, target: str):  # pragma: no cover - overridden
        raise NotImplementedError

    def _map_code(self, code: str) -> str:
        return language_map.map_language_code(self.name, code, self._lang_dict)

    def _get_client(self, source_language: str, target_language: str):
        if not self.is_available():
            missing = ", ".join(v for v in self.required_env if not os.getenv(v))
            raise PermanentTranslationError(f"{self.name} is not configured (missing {missing})")
        key = (self._map_code(source_language), self._map_code(target_language))
        client = self._clients.get(key)
        if client is not None:
            return client
        with self._lock:
            client = self._clients.get(key)
            if client is None:
                client = self._construct_client(*key)
                self._clients[key] = client
        return client

    async def translate(self, text: str, source_language: str, target_language: str) -> str:
        def _work() -> str:
            client = self._get_client(source_language, target_language)
            return str(client.translate(str(text)))

        try:
            return await asyncio.wait_for(asyncio.to_thread(_work), timeout=CALL_TIMEOUT_SECONDS)
        except asyncio.TimeoutError as exc:
            raise TransientTranslationError(f"{self.name} timed out") from exc
        except PermanentTranslationError:
            raise
        except Exception as exc:
            raise TransientTranslationError(str(exc)) from exc

    async def translate_batch(
        self, texts: t.List[str], source_language: str, target_language: str
    ) -> t.List[str]:
        def _work() -> t.List[str]:
            client = self._get_client(source_language, target_language)
            if hasattr(client, "translate_batch"):
                return [str(item) for item in client.translate_batch(list(texts))]
            return [str(client.translate(str(item))) for item in texts]

        try:
            return await asyncio.wait_for(asyncio.to_thread(_work), timeout=BATCH_TIMEOUT_SECONDS)
        except asyncio.TimeoutError as exc:
            raise TransientTranslationError(f"{self.name} timed out") from exc
        except PermanentTranslationError:
            raise
        except Exception as exc:
            raise TransientTranslationError(str(exc)) from exc


class DeeplBackend(_KeyedDeepTranslatorBackend):
    name = "deepl"
    display_name = "DeepL"
    required_env = ("DEEPL_API_KEY",)

    def _lang_dict(self) -> t.Dict[str, str]:
        return DEEPL_LANGUAGE_TO_CODE

    def _construct_client(self, source: str, target: str):
        # Free-tier keys end in ":fx" -- use_free_api=True (the
        # deep-translator default) already routes those to the free
        # endpoint automatically, so it's left at its default rather
        # than hard-coded either way.
        return DeeplTranslator(source=source, target=target, api_key=os.getenv("DEEPL_API_KEY"))


class MicrosoftBackend(_KeyedDeepTranslatorBackend):
    name = "microsoft"
    display_name = "Microsoft Translator"
    required_env = ("MICROSOFT_API_KEY",)

    def _lang_dict(self) -> t.Dict[str, str]:
        # No static language-code table ships with deep-translator for
        # Microsoft (unlike DeepL/Baidu/Papago) -- build one lazily from
        # a throwaway client using always-valid defaults ("auto"/"en"),
        # cached process-wide by language_map after the first call.
        try:
            probe = MicrosoftTranslator(
                source="auto",
                target="en",
                api_key=os.getenv("MICROSOFT_API_KEY"),
                region=os.getenv("MICROSOFT_REGION"),
            )
            return probe.get_supported_languages(as_dict=True) or {}
        except Exception:
            return {}

    def _construct_client(self, source: str, target: str):
        return MicrosoftTranslator(
            source=source,
            target=target,
            api_key=os.getenv("MICROSOFT_API_KEY"),
            region=os.getenv("MICROSOFT_REGION"),
        )


class PapagoBackend(_KeyedDeepTranslatorBackend):
    """Especially strong for Korean -- worth reaching for given this
    bot's existing Korean-specific handling elsewhere (see
    utils/handler.py's channel routing)."""

    name = "papago"
    display_name = "Papago"
    required_env = ("PAPAGO_CLIENT_ID", "PAPAGO_SECRET_KEY")

    def _lang_dict(self) -> t.Dict[str, str]:
        return PAPAGO_LANGUAGE_TO_CODE

    def _construct_client(self, source: str, target: str):
        return PapagoTranslator(
            client_id=os.getenv("PAPAGO_CLIENT_ID"),
            secret_key=os.getenv("PAPAGO_SECRET_KEY"),
            source=source,
            target=target,
        )


class BaiduBackend(_KeyedDeepTranslatorBackend):
    name = "baidu"
    display_name = "Baidu Translate"
    required_env = ("BAIDU_APP_ID", "BAIDU_APP_KEY")

    def _lang_dict(self) -> t.Dict[str, str]:
        return BAIDU_LANGUAGE_TO_CODE

    def _construct_client(self, source: str, target: str):
        return BaiduTranslator(
            appid=os.getenv("BAIDU_APP_ID"), appkey=os.getenv("BAIDU_APP_KEY"), source=source, target=target
        )


def register(reg) -> None:
    reg.register("deepl", DeeplBackend, display_name="DeepL")
    reg.register("microsoft", MicrosoftBackend, display_name="Microsoft Translator")
    reg.register("papago", PapagoBackend, display_name="Papago")
    reg.register("baidu", BaiduBackend, display_name="Baidu Translate")

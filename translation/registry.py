"""Central provider registry.

Backends register a factory (not an instance) so optional dependencies
stay lazy: importing translation.registry never imports googletrans /
deep_translator / translators, so a missing optional package can never
break bot startup -- it just makes that one provider unavailable.
"""
import logging
import threading
import typing as t

from .base import TranslationBackend

logger = logging.getLogger(__name__)


class ProviderRegistry:
    def __init__(self) -> None:
        self._factories: t.Dict[str, t.Callable[[], TranslationBackend]] = {}
        self._display_names: t.Dict[str, str] = {}
        self._instances: t.Dict[str, TranslationBackend] = {}
        self._lock = threading.Lock()

    def register(
        self,
        name: str,
        factory: t.Callable[[], TranslationBackend],
        display_name: t.Optional[str] = None,
    ) -> None:
        self._factories[name] = factory
        self._display_names[name] = display_name or name

    def get_provider(self, name: str) -> TranslationBackend:
        if name not in self._factories:
            raise KeyError(f"Unknown translation provider: {name!r}")
        with self._lock:
            instance = self._instances.get(name)
            if instance is None:
                instance = self._factories[name]()
                self._instances[name] = instance
        return instance

    def is_provider_available(self, name: str) -> bool:
        if name not in self._factories:
            return False
        try:
            provider = self.get_provider(name)
            return bool(provider.is_available())
        except Exception:
            logger.debug("Provider %s failed availability check", name, exc_info=True)
            return False

    def get_available_providers(self) -> t.List[str]:
        return [name for name in self._factories if self.is_provider_available(name)]

    def get_display_name(self, name: str) -> str:
        return self._display_names.get(name, name)

    def all_registered(self) -> t.List[str]:
        return list(self._factories.keys())


registry = ProviderRegistry()


def register_default_providers(reg: ProviderRegistry = registry) -> None:
    from .providers import deep_translator_backend, googletrans_backend, translators_backend

    googletrans_backend.register(reg)
    deep_translator_backend.register(reg)
    translators_backend.register(reg)


register_default_providers()

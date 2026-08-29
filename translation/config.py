"""Persistent translation settings.

Backed by a small JSON file (config/translation_settings.json) so the
admin-configured default engine survives a bot restart. A missing or
corrupted file is never fatal -- we fall back to DEFAULT_CONFIG and keep
running; the file is (re)written the next time something changes it.

Never store API keys/secrets here -- see translation/providers/*.py,
which read credentials from environment variables instead.
"""
import json
import os
import threading
import typing as t
from pathlib import Path

DEFAULT_SETTINGS_PATH = (
    Path(__file__).resolve().parent.parent / "config" / "translation_settings.json"
)

DEFAULT_CONFIG: t.Dict[str, t.Any] = {
    "default_engine": "googletrans",
    "auto_engine_order": [
        "googletrans",
        "deep-google",
        "translators-google",
        "translators-bing",
        "deep-mymemory",
        "translators-mymemory",
        "translators-yandex",
    ],
    "retry_delays": [2, 4, 7],
    "request_delay": 0.2,
    "max_concurrency": 3,
    "provider_concurrency": {},
    "min_recoverable_chunk_chars": 120,
}


class TranslationSettings:
    """Loads/saves translation config. One instance is used as the global
    process-wide settings (see `settings` below); tests construct their
    own instance pointed at a temp file so they never touch the real
    config/translation_settings.json."""

    def __init__(self, path: t.Optional[t.Union[str, Path]] = None) -> None:
        self._path = Path(path) if path else DEFAULT_SETTINGS_PATH
        self._data: t.Dict[str, t.Any] = dict(DEFAULT_CONFIG)
        self._lock = threading.Lock()
        self.load()

    def load(self) -> t.Dict[str, t.Any]:
        try:
            if self._path.exists():
                with open(self._path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                if isinstance(loaded, dict):
                    for key in DEFAULT_CONFIG:
                        if key in loaded:
                            self._data[key] = loaded[key]
        except Exception:
            # Missing file, corrupted JSON, permission error, whatever --
            # never crash the bot over settings. Keep whatever defaults
            # we already had.
            pass
        return dict(self._data)

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = self._path.with_suffix(".tmp")
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(self._data, f, indent=2, sort_keys=True)
            os.replace(tmp_path, self._path)
        except Exception:
            # A failed write should never crash the bot -- the in-memory
            # value still takes effect for this process, it just won't
            # survive a restart.
            pass

    # -- accessors ---------------------------------------------------
    @property
    def default_engine(self) -> str:
        return str(self._data.get("default_engine") or "googletrans")

    def set_default_engine(self, engine: str) -> None:
        with self._lock:
            self._data["default_engine"] = engine
            self._save()

    @property
    def auto_engine_order(self) -> t.List[str]:
        return list(self._data.get("auto_engine_order") or [])

    @property
    def retry_delays(self) -> t.List[float]:
        return list(self._data.get("retry_delays") or [2, 4, 7])

    @property
    def request_delay(self) -> float:
        try:
            return float(self._data.get("request_delay", 0.2))
        except (TypeError, ValueError):
            return 0.2

    @property
    def max_concurrency(self) -> int:
        try:
            return max(1, int(self._data.get("max_concurrency", 3)))
        except (TypeError, ValueError):
            return 3

    @property
    def provider_concurrency(self) -> t.Dict[str, int]:
        raw = self._data.get("provider_concurrency") or {}
        return {k: int(v) for k, v in raw.items() if str(v).isdigit()}

    @property
    def min_recoverable_chunk_chars(self) -> int:
        try:
            return max(1, int(self._data.get("min_recoverable_chunk_chars", 120)))
        except (TypeError, ValueError):
            return 120


# Process-wide singleton used by the manager/registry/Discord cogs.
settings = TranslationSettings()

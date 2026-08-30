"""Persists the globally-configured default translation engine so it
survives bot restarts.

Deliberately a small standalone JSON file rather than a new Mongo
collection: this project already has a database (utils/connector.py /
databases/*) for novel/library/ban data, but nothing resembling a
key-value settings store, and one file is simpler than adding a new
collection + schema for a single string value. Never crashes the bot --
a missing or corrupted file just falls back to FALLBACK_DEFAULT_ENGINE.
"""
import json
import logging
import os
import threading

logger = logging.getLogger("raizel_bot.translator")

FALLBACK_DEFAULT_ENGINE = "googletrans"

_SETTINGS_DIR = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "config")
)
_SETTINGS_PATH = os.path.join(_SETTINGS_DIR, "translation_settings.json")

_lock = threading.Lock()


def _read_raw() -> dict:
    try:
        with open(_SETTINGS_PATH, "r", encoding="utf-8") as fp:
            data = json.load(fp)
        if not isinstance(data, dict):
            raise ValueError("translation_settings.json did not contain a JSON object")
        return data
    except FileNotFoundError:
        return {}
    except (json.JSONDecodeError, ValueError, OSError):
        # Corrupted / unreadable file -- never let this take the bot down.
        return {}


def get_default_engine() -> str:
    data = _read_raw()
    engine = data.get("default_engine")
    if not isinstance(engine, str) or not engine.strip():
        return FALLBACK_DEFAULT_ENGINE
    engine = engine.strip()

    # BUG FIX (found in review, section 26): this used to return whatever
    # string was on disk with no sanity check against the actual engine
    # registry. A stale/corrupted/hand-edited value (or an engine that
    # existed in a previous version of the bot and was since removed)
    # would silently become the resolved "Default" engine, and only fail
    # much later -- inside an actual translation job -- with a message
    # that doesn't make it obvious the *persisted setting itself* is bad.
    # Validating here means a bad persisted value degrades exactly like a
    # missing/corrupted file: a logged warning and a safe fallback, never
    # a crash and never a confusing failure deep in a translation job.
    try:
        from translator import registry
        if registry.get_spec(engine) is None:
            logger.warning(
                "Persisted default translation engine is not a known engine "
                "engine=%s falling_back_to=%s", engine, FALLBACK_DEFAULT_ENGINE,
            )
            return FALLBACK_DEFAULT_ENGINE
    except ImportError:
        # translator.registry not importable for some reason (e.g. during
        # partial/circular-import edge cases) -- don't let a validation
        # nicety crash a plain settings read.
        pass

    return engine


def set_default_engine(engine: str) -> None:
    """Atomically persist the new default engine (write-to-temp then
    os.replace, same pattern core/bot.py already uses for healthcheck.json,
    so a crash mid-write can never leave a half-written/corrupt file)."""
    if not engine or not isinstance(engine, str):
        raise ValueError("engine must be a non-empty string")

    with _lock:
        os.makedirs(_SETTINGS_DIR, exist_ok=True)
        data = _read_raw()
        data["default_engine"] = engine.strip()
        tmp_path = f"{_SETTINGS_PATH}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as fp:
            json.dump(data, fp, indent=2)
        os.replace(tmp_path, _SETTINGS_PATH)


def settings_path() -> str:
    return _SETTINGS_PATH

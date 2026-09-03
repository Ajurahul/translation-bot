"""On-demand and startup health checks for translation providers.

This project's Auto mode races every currently-available engine
concurrently per job rather than working through a fixed order with a
single "currently preferred" engine and per-engine cooldown timers (see
translation/manager.py's module docstring). To keep that architecture
intact while still delivering the operationally important pieces of
graduated failure handling:

  * A provider that fails a health probe (or a real quota/rate-limit
    failure encountered during an actual translation job -- see
    TranslationManager) is marked *session-disabled* here, with a
    reason. This is the equivalent of "disabled for the rest of the
    process" -- there is no timer; it only clears on a later successful
    probe.
  * `is_session_disabled()` is consulted by
    TranslationManager._auto_candidates() so a session-disabled engine
    is excluded from Auto's race until it's proven healthy again.
  * There is deliberately no per-engine "3 consecutive ordinary
    failures -> 5 minute cooldown" timer and no single class-level
    "preferred" engine -- those concepts don't map cleanly onto a
    design that already tries every healthy candidate on every "engine
    needed" event rather than sticking to one at a time. A transient
    (non-quota) failure during a real job is instead handled the way it
    already was: excluded from that one job's `failed_engines` set,
    with no effect on any other job or on future jobs.

`run_health_check()` is what backs both the automatic check on cog load
and the `.enginecheck` admin command.
"""
import asyncio
import logging
import threading
import time
import typing as t

from .registry import ProviderRegistry
from .registry import registry as global_registry

logger = logging.getLogger(__name__)

PROBE_TEXT = "hello"
PROBE_SOURCE = "en"
PROBE_TARGET = "es"
PROBE_TIMEOUT_SECONDS = 20.0

_lock = threading.Lock()
_session_disabled: t.Dict[str, str] = {}
_last_checked: t.Dict[str, float] = {}


def is_session_disabled(name: str) -> bool:
    with _lock:
        return name in _session_disabled


def disabled_reason(name: str) -> t.Optional[str]:
    with _lock:
        return _session_disabled.get(name)


def mark_session_disabled(name: str, reason: str) -> None:
    """Disable `name` for the rest of this process (until a later
    successful health check re-enables it). Safe to call from real job
    failure handling as well as explicit health checks -- see
    TranslationManager's quota-failure handling."""
    with _lock:
        _session_disabled[name] = str(reason)[:300]
        _last_checked.setdefault(name, time.time())


def mark_session_enabled(name: str) -> None:
    with _lock:
        _session_disabled.pop(name, None)


def clear_all() -> None:
    """Test hook."""
    with _lock:
        _session_disabled.clear()
        _last_checked.clear()


class EngineCheckResult(t.NamedTuple):
    engine: str
    label: str
    configured: bool  # package installed / required env vars present
    ok: bool  # probe succeeded (meaningless if not configured)
    changed: bool  # status flipped (recovered, or newly failed) this check
    reason: t.Optional[str]


async def _probe_one(reg: ProviderRegistry, name: str) -> EngineCheckResult:
    label = reg.get_display_name(name)
    try:
        provider = reg.get_provider(name)
        configured = bool(provider.is_available())
    except Exception:
        configured = False

    if not configured:
        # Missing dependency/credentials isn't a health *failure* -- it's
        # just not applicable right now. Leave any prior session-disable
        # state alone; there's nothing to (re)probe.
        return EngineCheckResult(name, label, False, False, False, "not configured")

    was_disabled = is_session_disabled(name)
    try:
        await asyncio.wait_for(
            provider.translate(PROBE_TEXT, PROBE_SOURCE, PROBE_TARGET),
            timeout=PROBE_TIMEOUT_SECONDS,
        )
        mark_session_enabled(name)
        with _lock:
            _last_checked[name] = time.time()
        return EngineCheckResult(name, label, True, True, was_disabled, None)
    except Exception as exc:
        reason = str(exc)[:300]
        mark_session_disabled(name, reason)
        return EngineCheckResult(name, label, True, False, not was_disabled, reason)


async def run_health_check(
    registry: t.Optional[ProviderRegistry] = None,
) -> t.List[EngineCheckResult]:
    """Probe every registered engine with one tiny real translation --
    including ones already session-disabled, so recovery gets detected
    and not just new failures. Probes run concurrently; one slow/hanging
    provider can't hold the others up (see PROBE_TIMEOUT_SECONDS)."""
    reg = registry or global_registry
    names = reg.all_registered()
    results = await asyncio.gather(*(_probe_one(reg, name) for name in names))
    return list(results)


def status_snapshot(registry: t.Optional[ProviderRegistry] = None) -> t.List[dict]:
    """Full status of every engine for a status command: configured,
    available (configured and not session-disabled), disabled reason,
    last time it was checked."""
    reg = registry or global_registry
    snapshot = []
    for name in reg.all_registered():
        try:
            provider = reg.get_provider(name)
            configured = bool(provider.is_available())
        except Exception:
            configured = False
        disabled = is_session_disabled(name)
        with _lock:
            last_checked = _last_checked.get(name)
        snapshot.append(
            {
                "name": name,
                "label": reg.get_display_name(name),
                "configured": configured,
                "available": configured and not disabled,
                "session_disabled": disabled,
                "reason": disabled_reason(name),
                "last_checked": last_checked,
            }
        )
    return snapshot


async def run_startup_check(registry: t.Optional[ProviderRegistry] = None) -> None:
    """Best-effort startup probe -- never raises. Intended to be fired
    from a cog's `cog_load()` without being awaited inline, so a slow or
    hanging provider can't delay the bot coming up."""
    try:
        results = await run_health_check(registry)
        failed = [r for r in results if r.configured and not r.ok]
        if failed:
            logger.info(
                "Translation engine startup check: %d engine(s) failed: %s",
                len(failed),
                ", ".join(f"{r.label} ({r.reason})" for r in failed),
            )
        else:
            logger.info("Translation engine startup check: all configured engines OK")
    except Exception:
        logger.info("Translation engine startup health check failed", exc_info=True)


__all__ = [
    "is_session_disabled",
    "disabled_reason",
    "mark_session_disabled",
    "mark_session_enabled",
    "clear_all",
    "EngineCheckResult",
    "run_health_check",
    "status_snapshot",
    "run_startup_check",
]

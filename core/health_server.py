"""Tiny HTTP endpoint for the EC2 *application status check*.

EC2 application status checks (EC2 console -> Instances -> Application status
checks) send an HTTP request to a port/path on the instance every 60 seconds and
compare the response code to a matcher (default: 200).

Design notes
------------
* Runs inside the bot's own asyncio loop (aiohttp is already a dependency), so
  if the event loop is blocked or the process is hung, the request times out and
  EC2 reports the application as impaired -- exactly what we want.
* Health reflects THIS process only (Discord gateway connection + loop alive).
  It deliberately does NOT call MongoDB, Mega or the translation engines, so a
  third-party outage can't mark the instance impaired.
* Binds to 0.0.0.0 by default. The EC2 check comes from a private ENI in your
  VPC, so binding to 127.0.0.1 would make every check fail.
"""
import logging
import math
import os
import typing as t

from aiohttp import web

if t.TYPE_CHECKING:  # pragma: no cover
    from core.bot import Raizel

log = logging.getLogger("raizel_bot")

HEALTH_HOST = os.getenv("HEALTH_HOST", "0.0.0.0")
HEALTH_PORT = int(os.getenv("HEALTH_PORT", "8080"))


def _evaluate(bot: "Raizel") -> t.Tuple[bool, dict]:
    latency = bot.latency
    latency_ok = not (latency is None or math.isnan(latency) or math.isinf(latency))
    closed = bot.is_closed()
    ready = bot.is_ready()

    healthy = (not closed) and ready and latency_ok
    if closed:
        state = "closed"
    elif not ready:
        state = "starting_or_reconnecting"
    elif not latency_ok:
        state = "gateway_unresponsive"
    else:
        state = "ok"

    body = {
        "status": "healthy" if healthy else "unhealthy",
        "state": state,
        "discord_ready": ready,
        "closed": closed,
        "latency_ms": round(latency * 1000) if latency_ok else None,
        "app_status": getattr(bot, "app_status", None),
        "uptime_seconds": int((__import__("datetime").datetime.utcnow() - bot.boot).total_seconds()),
    }
    return healthy, body


def build_app(bot: "Raizel") -> web.Application:
    async def health(_request: web.Request) -> web.Response:
        try:
            healthy, body = _evaluate(bot)
        except Exception:  # never let the probe itself crash
            log.exception("[health] evaluation failed")
            return web.json_response({"status": "unhealthy", "state": "error"}, status=503)
        return web.json_response(body, status=200 if healthy else 503)

    app = web.Application()
    app.router.add_get("/health", health)
    app.router.add_get("/", health)
    return app


async def start_health_server(bot: "Raizel") -> web.AppRunner:
    runner = web.AppRunner(build_app(bot), access_log=None)  # no log spam every 60s
    await runner.setup()
    site = web.TCPSite(runner, HEALTH_HOST, HEALTH_PORT)
    await site.start()
    log.info("[health] listening on http://%s:%s/health", HEALTH_HOST, HEALTH_PORT)
    return runner


async def stop_health_server(runner: t.Optional[web.AppRunner]) -> None:
    if runner is not None:
        try:
            await runner.cleanup()
        except Exception:
            log.exception("[health] error while stopping")

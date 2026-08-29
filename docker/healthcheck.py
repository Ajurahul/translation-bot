"""
Docker HEALTHCHECK helper.

Reuses the heartbeat file the bot already writes itself
(core/bot.py: Raizel.write_healthcheck), so this doesn't need to know
anything about Discord gateway internals -- it just checks that the bot
wrote a recent "running" heartbeat.

Exit 0 = healthy, exit 1 = unhealthy (Docker will mark the container
unhealthy / restart it, depending on your `restart` policy).
"""
import json
import sys
from datetime import datetime, timezone

HEALTH_FILE = "/app/logs/healthcheck.json"
MAX_AGE_SECONDS = 180  # matches scripts/auto-restart.sh's HEALTH_MAX_AGE_SECONDS

try:
    with open(HEALTH_FILE, "r", encoding="utf-8") as fp:
        data = json.load(fp)

    updated_at = datetime.fromisoformat(data["updated_at"])
    if updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=timezone.utc)

    age_seconds = (datetime.now(timezone.utc) - updated_at).total_seconds()

    if data.get("status") == "running" and not data.get("closed") and age_seconds < MAX_AGE_SECONDS:
        sys.exit(0)
except Exception:
    pass

sys.exit(1)

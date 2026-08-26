#!/usr/bin/env bash

set -u

# Cron on EC2 can run with a very small PATH.
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
LOG_DIR="$REPO_DIR/logs"
LOG_FILE="$LOG_DIR/bot.txt"
STARTUP_LOG="$LOG_DIR/bot_startup.log"
HEALTH_FILE="$LOG_DIR/healthcheck.json"
SESSION_NAME="ENTER"
BOT_CMD="python3 main.py"
HEALTH_MAX_AGE_SECONDS=180
BOT_START_WAIT_SECONDS=10
RUN_USER="${USER:-$(id -un 2>/dev/null || echo cron)}"

PYTHON_BIN="$(command -v python3 2>/dev/null || true)"
TMUX_BIN="$(command -v tmux 2>/dev/null || true)"
GIT_BIN="$(command -v git 2>/dev/null || true)"

mkdir -p "$LOG_DIR"

log() {
  local nowtime
  nowtime="$(date '+%Y-%m-%d %H:%M:%S')"
  echo "$RUN_USER : $1 at $nowtime" >> "$LOG_FILE"
}

if [[ -z "$PYTHON_BIN" || -z "$TMUX_BIN" || -z "$GIT_BIN" ]]; then
  log "missing dependency python3/tmux/git (PATH=$PATH)"
  exit 1
fi

get_bot_pid() {
  # Use the first matching bot process; empty output means not running.
  pgrep -f "$BOT_CMD" | head -n 1
}

is_tmux_pane_dead() {
  "$TMUX_BIN" list-panes -t "$SESSION_NAME" -F '#{pane_dead}' 2>/dev/null | grep -qx '1'
}

get_mtime_epoch() {
  local file="$1"
  if stat -c %Y "$file" >/dev/null 2>&1; then
    stat -c %Y "$file"
  else
    stat -f %m "$file"
  fi
}

is_healthcheck_stale() {
  [[ -f "$HEALTH_FILE" ]] || return 0
  local now_epoch mtime age
  now_epoch="$(date +%s)"
  mtime="$(get_mtime_epoch "$HEALTH_FILE" 2>/dev/null || echo 0)"
  age=$((now_epoch - mtime))
  (( age > HEALTH_MAX_AGE_SECONDS ))
}

restart_bot() {
  # Requested legacy restart flow.
  pkill -f "$BOT_CMD" 2>/dev/null || true
  pgrep python3 >/dev/null 2>&1 && killall python3
  "$TMUX_BIN" kill-session -t "$SESSION_NAME" 2>/dev/null || true

  cd "$REPO_DIR" || exit 1
  "$GIT_BIN" pull --ff-only || log "git pull failed"

  if ! "$TMUX_BIN" new-session -d -s "$SESSION_NAME"; then
    log "failed to create tmux session"
    exit 1
  fi

  # Redirect bot stdout+stderr to a dedicated startup log so crashes are visible.
  local bot_launch_cmd="cd '$REPO_DIR' && $PYTHON_BIN main.py >> '$STARTUP_LOG' 2>&1"
  "$TMUX_BIN" send-keys -t "$SESSION_NAME":0 "$bot_launch_cmd" ENTER

  sleep "$BOT_START_WAIT_SECONDS"
  if pgrep -f "[p]ython3 main.py" >/dev/null 2>&1; then
    log "started bot"
  else
    log "failed to start bot in tmux — last startup output below:"
    # Append last 40 lines of startup log so the reason is visible in bot.txt
    if [[ -f "$STARTUP_LOG" ]]; then
      tail -n 40 "$STARTUP_LOG" | while IFS= read -r line; do
        log "  STARTUP| $line"
      done
    else
      log "  STARTUP| (no output captured — check tmux session '$SESSION_NAME')"
    fi
    exit 1
  fi
}

PID="$(get_bot_pid || true)"

if [[ -z "$PID" ]]; then
  restart_bot
  exit 0
fi

if ! ps -p "$PID" > /dev/null 2>&1; then
  restart_bot
  exit 0
fi

# Zombie process can keep a PID alive while the app is effectively dead.
if ps -o stat= -p "$PID" 2>/dev/null | grep -q 'Z'; then
  log "detected zombie bot process ($PID), restarting"
  restart_bot
  exit 0
fi

if ! "$TMUX_BIN" has-session -t "$SESSION_NAME" 2>/dev/null; then
  log "tmux session missing, restarting"
  restart_bot
  exit 0
fi

if is_tmux_pane_dead; then
  log "tmux pane dead, restarting"
  restart_bot
  exit 0
fi

if is_healthcheck_stale; then
  log "healthcheck stale or missing, restarting"
  restart_bot
  exit 0
fi

log "health check ok (pid=$PID)"

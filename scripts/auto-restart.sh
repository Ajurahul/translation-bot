#!/usr/bin/env bash

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
LOG_DIR="$REPO_DIR/logs"
LOG_FILE="$LOG_DIR/bot.txt"
HEALTH_FILE="$LOG_DIR/healthcheck.json"
SESSION_NAME="ENTER"
BOT_CMD="python3 main.py"
HEALTH_MAX_AGE_SECONDS=180

mkdir -p "$LOG_DIR"

log() {
  local nowtime
  nowtime="$(date '+%Y-%m-%d %H:%M:%S')"
  echo "$USER : $1 at $nowtime" >> "$LOG_FILE"
}

get_bot_pid() {
  # Use the first matching bot process; empty output means not running.
  pgrep -f "$BOT_CMD" | head -n 1
}

is_tmux_pane_dead() {
  tmux list-panes -t "$SESSION_NAME" -F '#{pane_dead}' 2>/dev/null | grep -qx '1'
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
  # Only kill the bot process/session, never all python/tmux processes.
  pkill -f "$BOT_CMD" 2>/dev/null || true
  tmux kill-session -t "$SESSION_NAME" 2>/dev/null || true

  cd "$REPO_DIR" || exit 1
  git pull --ff-only || log "git pull failed"

  tmux new-session -d -s "$SESSION_NAME" "cd '$REPO_DIR' && exec $BOT_CMD"
  log "started bot"
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

if ! tmux has-session -t "$SESSION_NAME" 2>/dev/null; then
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

#!/usr/bin/env bash

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
LOG_DIR="$HOME/logs"
LOG_FILE="$LOG_DIR/translation-bot.log"

mkdir -p "$LOG_DIR"

PID=$(ps -ef | grep -v grep | grep "python3 main.py" | awk '{print $2}')

if ! ps -p "$PID" > /dev/null
then
  pkill -f tmux
  killall python3
  sleep 1
  pgrep python3 && killall python3
  tmux new-session -d -s ENTER
  tmux detach -s ENTER
  tmux send-keys -t 0 "cd $REPO_DIR;python3 main.py" ENTER
  nowtime=$(date)
  echo "$USER : started bot at $nowtime" >> "$LOG_FILE"
#else
# echo "already running"
fi
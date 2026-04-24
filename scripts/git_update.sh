#!/usr/bin/env bash

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
LOG_DIR="$REPO_DIR/logs"
LOG_FILE="$LOG_DIR/bot.txt"

mkdir -p "$LOG_DIR"

cd "$REPO_DIR" || exit 1
if [ -d "$REPO_DIR/.git" ]; then
  git pull
  echo "$USER : Pulled latest changes from git." >> "$LOG_FILE"
else
  git clone https://github.com/Ajurahul/translation-bot.git "$REPO_DIR"
  echo "$USER : Cloned repository from git." >> "$LOG_FILE"
fi

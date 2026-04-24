#!/usr/bin/env bash

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
LOG_DIR="$REPO_DIR/logs"
LOG_FILE="$LOG_DIR/bot.txt"

mkdir -p "$LOG_DIR"

cd "$REPO_DIR" || exit 1
rm -rf translation-bot
sleep 1
git clone  https://github.com/Ajurahul/translation-bot.git
echo "$USER : Updated git. bot will restart after this" >> "$LOG_FILE"
cd translation-bot

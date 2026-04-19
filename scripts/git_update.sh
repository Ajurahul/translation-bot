#!/usr/bin/env bash

BASE_DIR="${BASE_DIR:-$HOME}"
LOG_DIR="/home/logs"
LOG_FILE="$LOG_DIR/translation-bot.log"

mkdir -p "$LOG_DIR"

cd "$BASE_DIR" || exit 1
rm -rf translation-bot
sleep 1
git clone  https://github.com/Ajurahul/translation-bot.git
echo "$USER : Updated git. bot will restart after this" >> "$LOG_FILE"
cd translation-bot

#!/usr/bin/env bash

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
LOG_DIR="$REPO_DIR/logs"
LOG_FILE="$LOG_DIR/bot.txt"

mkdir -p "$LOG_DIR"

nowtime=$(date)
echo "$USER : Restarting server at $nowtime" >> "$LOG_FILE"
aws ec2 reboot-instances --instance-ids i-00d5a946cd73a5150 --profile admin >> "$LOG_FILE"
sudo systemctl reboot
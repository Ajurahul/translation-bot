#!/usr/bin/env bash

LOG_DIR="$HOME/logs"
LOG_FILE="$LOG_DIR/translation-bot.log"

mkdir -p "$LOG_DIR"

nowtime=$(date)
echo "$USER : Restarting server at $nowtime" >> "$LOG_FILE"
aws ec2 reboot-instances --instance-ids i-00d5a946cd73a5150 --profile admin >> "$LOG_FILE"
sudo systemctl reboot
nowtime=$(date)
echo "$USER : Restarting server at $nowtime">>/home/ec2-user/logs
aws ec2 reboot-instances --instance-ids i-00d5a946cd73a5150 --profile admin >>/home/ec2-user/logs
sudo systemctl reboot
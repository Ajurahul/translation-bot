cd /home/ubuntu/
rm -rf translation-bot
sleep 1
git clone  git@github.com:Ajurahul/translation-bot.git
echo "$USER : Updated git. bot will restart after this">>/home/ubuntu/logs
cd translation-bot
pip install -r requirements.txt
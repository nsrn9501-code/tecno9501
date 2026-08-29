#!/usr/bin/env bash
cd "$(dirname "$0")"
# قتل أي نسخة قديمة أولاً حتى لا تتكرر النسخ وتتعارض
for oldpid in $(pgrep -f "venv/bin/python run.py" 2>/dev/null); do
  kill -9 "$oldpid" 2>/dev/null
done
sleep 1
if [ -f bot.pid ] && kill -0 "$(cat bot.pid)" 2>/dev/null; then
  echo "البوت شغال بالفعل (PID $(cat bot.pid))"
  exit 0
fi
# تشغيل معزول عن الطرفية حتى لا يموت بانتهاء الجلسة
setsid nohup ./venv/bin/python run.py > bot.log 2>&1 < /dev/null &
echo $! > bot.pid
sleep 4
if kill -0 "$(cat bot.pid)" 2>/dev/null; then
  echo "✅ تم تشغيل البوت — السجل: bot.log"
else
  echo "❌ فشل التشغيل — راجع bot.log"
fi

#!/usr/bin/env bash
cd "$(dirname "$0")"
# إيقاف النسخة الحالية (إن وُجدت)
if [ -f bot.pid ] && kill -0 "$(cat bot.pid)" 2>/dev/null; then
  kill "$(cat bot.pid)" 2>/dev/null
  sleep 2
fi
# تشغيل نسخة جديدة
setsid nohup ./venv/bin/python run.py > bot.log 2>&1 < /dev/null &
echo $! > bot.pid
echo "restarted -> $(cat bot.pid)"

#!/usr/bin/env bash
# حارس البوت: يعيد تشغيله تلقائياً إذا مات
cd "$(dirname "$0")"
while true; do
  if [ ! -f bot.pid ] || ! kill -0 "$(cat bot.pid)" 2>/dev/null; then
    echo "[$(date '+%H:%M:%S')] البوت مات، إعادة تشغيل..."
    setsid nohup ./venv/bin/python run.py >> bot.log 2>&1 < /dev/null &
    echo $! > bot.pid
    echo "[$(date '+%H:%M:%S')] أُعيد تشغيله (PID $(cat bot.pid))"
  fi
  sleep 10
done

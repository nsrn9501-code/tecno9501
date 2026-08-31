#!/usr/bin/env bash
cd "$(dirname "$0")"
# في Docker/HF Spaces: أوقف العملية الحالية وابدأ جديدة
if [ -f bot.pid ] && kill -0 "$(cat bot.pid)" 2>/dev/null; then
  kill "$(cat bot.pid)" 2>/dev/null
  sleep 2
fi
# تشغيل نسخة جديدة (يعمل مع venv أو Docker)
PYTHON="$(which python3 || which python)"
setsid nohup "$PYTHON" run.py > bot.log 2>&1 < /dev/null &
echo $! > bot.pid
echo "restarted -> $(cat bot.pid)"

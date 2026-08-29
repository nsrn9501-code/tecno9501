#!/usr/bin/env bash
if [ -f bot.pid ] && kill -0 "$(cat bot.pid)" 2>/dev/null; then
  kill "$(cat bot.pid)"
  rm -f bot.pid
  echo "🛑 تم إيقاف البوت"
else
  echo "البوت غير شغال"
fi

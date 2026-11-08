#!/bin/sh
# 每日 WAL 只读 stat（lead 2026-11-07T094403Z-home-4e67 第四条：WAL 复查提到每天）
# 只 stat 文件大小，不连库、不 checkpoint、不改权限；同日已记录则跳过。
# 已批的每周 wal_checkpoint 与 724h 复查仍保留，本脚本是补充监控，不是修复。
LOG=/var/log/meme-radar-wal-daily.log
TODAY=$(date -u +%F)
case "$1" in --force) FORCE=1;; *) FORCE=0;; esac
if [ "$FORCE" = 0 ] && [ -f "$LOG" ] && tail -n 1 "$LOG" | grep -q "^$TODAY,"; then
    tail -n 1 "$LOG"; exit 0
fi
TS=$(date -u +%FT%TZ)
echo "$TS,daily_wal_stat" >> "$LOG"
for f in /var/lib/meme-radar/*-wal /var/lib/predict-arb/*-wal /var/lib/stock-radar/*-wal; do
    [ -e "$f" ] || continue
    echo "$TS,$f,$(stat -c %s "$f"),$(stat -c %Y "$f")" >> "$LOG"
done
# meme-radar 主库为 delete 日志模式，无 -wal 文件，显式记一行便于对照
echo "$TS,/var/lib/meme-radar/meme-radar.sqlite,journal_mode=$(sudo -u meme-radar python3 -I -c "import sqlite3;print(sqlite3.connect('file:/var/lib/meme-radar/meme-radar.sqlite?mode=ro',uri=True).execute('pragma journal_mode').fetchone()[0])" 2>/dev/null || echo unknown),0" >> "$LOG"
tail -n 6 "$LOG"

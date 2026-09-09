#!/usr/bin/env bash
# 把某一天的 iThome 版整篇放進剪貼簿，到 iThome 編輯器 Ctrl+V 就好。
#
# 用法：./scripts/copy-day.sh 1
#
# 會先重跑 make-ithome.py，確保複製到的是最新內容
#（8/31 踩過：改了 _posts 但 ithome/ 還是舊的，差點貼錯版本）。
set -euo pipefail
cd "$(dirname "$0")/.."
DRAFTS="$HOME/Documents/ironman-drafts"

[ $# -eq 1 ] || { echo "用法: $0 <day>"; exit 1; }
day=$(printf '%02d' "$1")

command -v wl-copy >/dev/null || { echo "❌ 沒有 wl-copy（pacman -S wl-clipboard）"; exit 1; }

# 先同步，避免貼到舊版
python3 scripts/make-ithome.py >/dev/null

src=$(ls "$DRAFTS"/ithome/day${day}-*.md 2>/dev/null | head -1) || true
[ -n "$src" ] || { echo "❌ 找不到 Day $1（$DRAFTS/ithome/day${day}-*.md）"; exit 1; }

setsid -f wl-copy < "$src"   # detach，避免呼叫端（如 Claude）結束時連帶被殺
n=$(wl-paste | wc -c)
title=$(head -1 "$src" | sed 's/^# //')

echo "✅ 已複製進剪貼簿：$title"
echo "   $(basename "$src")　${n} 字元"
echo
echo "⏱ 提醒：先 ./scripts/publish-day.sh $1 + git push 讓部落格上線，再貼 iThome。"
echo "   反過來的話，讀者點文末「本篇原文」會 404。"

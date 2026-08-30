#!/usr/bin/env bash
# 把某一天的 iThome 文章在部落格上公開（改 published: false → true 並推上去）。
#
# 用法：./scripts/publish-day.sh 1        # 公開 Day 1
#       ./scripts/publish-day.sh 1 2 3    # 一次公開多篇
#       ./scripts/publish-day.sh --list   # 看目前狀態
#
# 流程：iThome 貼文 → 跑這支 → 部落格那篇同時上線 → 文末連結才不會 404
set -euo pipefail
cd "$(dirname "$0")/.."
# 🔴 稿件放在 repo 外面（這個 repo 是 public）。發文當天才複製進來。
DRAFTS="$HOME/Documents/ironman-drafts"

status() {
  printf '%-4s %-9s %s\n' Day 狀態 標題
  for f in "$DRAFTS"/_posts/*.md; do
    grep -q 'OpenShift AI 入門 30 天' <(head -c 400 "$f") || continue
    t=$(sed -n 's/^title: "\(.*\)"$/\1/p' "$f" | head -1)
    d=$(sed -n 's/.*Day \([0-9]*\).*/\1/p' <<<"$t" | head -1)
    if [ -f "_posts/$(basename "$f")" ]; then s='✅ 已公開'; else s='未公開'; fi
    printf '%-4s %-9s %s\n' "$d" "$s" "$t"
  done | sort -n
}

[ "${1:-}" = "--list" ] && { status; exit 0; }
[ $# -ge 1 ] || { echo "用法: $0 <day> [day...]  或  $0 --list"; exit 1; }

changed=()
for day in "$@"; do
  src=$(grep -l "Day ${day}：" "$DRAFTS"/_posts/*.md | head -1)
  [ -n "$src" ] || { echo "❌ 找不到 Day ${day}"; exit 1; }
  dst="_posts/$(basename "$src")"
  if [ -f "$dst" ]; then echo "－ Day ${day} 已經在部落格裡了"; continue; fi
  sed '/^published: false/d' "$src" > "$dst"
  echo "✅ Day ${day} → 複製進部落格　$(basename "$dst")"
  changed+=("$dst")
done

[ ${#changed[@]} -eq 0 ] && { echo "沒有東西要改"; exit 0; }

# 同步 iThome 版（連結會跟著文章內容變）
python3 scripts/make-ithome.py >/dev/null

git add -A
git commit -q -m "公開 Day $*"
echo
echo "已 commit。確認沒問題就推："
echo "  git push"

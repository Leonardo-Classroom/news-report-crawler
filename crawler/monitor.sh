#!/bin/bash
# 即時監測爬蟲 + 記者探索進度。
# 用法：
#   ./monitor.sh                  # 預設每 10 秒刷新一次
#   ./monitor.sh 30                # 每 30 秒刷新一次
#   LOG=/path/to/crawl.log ./monitor.sh   # 指定要 tail 的 log（預設用共用的 crawl.log，
#                                          # 爬蟲與探索腳本的 log() 都會寫進同一份）

set -u
cd "$(dirname "$0")/.."   # 專案根目錄（dataset/ 的上一層）

INTERVAL="${1:-10}"
LOG="${LOG:-dataset/_state/crawl.log}"
REPORTERS="dataset/記者清單.json"
COMPLETED="dataset/_state/robust_completed.log"

prev_total=0
prev_reporters=0
prev_ts=$(date +%s)

while true; do
  now_ts=$(date +%s)
  now_str=$(date "+%Y-%m-%d %H:%M:%S")

  total=$(timeout 15 find dataset -name "*.txt" 2>/dev/null | wc -l)
  ct=$(timeout 10 find "dataset/中時新聞網" -name "*.txt" 2>/dev/null | wc -l)
  udn=$(timeout 10 find "dataset/聯合新聞網" -name "*.txt" 2>/dev/null | wc -l)
  ctee=$(timeout 10 find "dataset/工商時報" -name "*.txt" 2>/dev/null | wc -l)

  elapsed=$(( now_ts - prev_ts ))
  delta=$(( total - prev_total ))
  rate_min="0"
  if [ "$elapsed" -gt 0 ] && [ "$prev_total" -gt 0 ]; then
    rate_min=$(awk -v d="$delta" -v s="$elapsed" 'BEGIN{printf "%.1f", d/s*60}')
  fi

  total_reporters=0
  if [ -f "$REPORTERS" ]; then
    total_reporters=$(python3 -c "import json; print(len(json.load(open('$REPORTERS', encoding='utf-8'))))" 2>/dev/null || echo 0)
  fi
  reporter_delta=$(( total_reporters - prev_reporters ))

  # 先把整份畫面組好存進變數，等資料都抓齊了才清畫面 + 印出來，
  # 避免「先清空、再等資料」造成的空白閃爍。
  frame=""
  frame+="======================================================\n"
  frame+=" 爬蟲即時監測  $now_str\n"
  frame+="======================================================\n"
  frame+=" 總文章數        : $total\n"
  frame+="   中時新聞網     : $ct\n"
  frame+="   聯合新聞網     : $udn\n"
  frame+="   工商時報       : $ctee\n"
  frame+="------------------------------------------------------\n"
  frame+=" 本次刷新新增     : +$delta 篇（${elapsed}s 內）\n"
  frame+=" 目前速度         : 約 ${rate_min} 篇/分鐘\n"
  frame+="------------------------------------------------------\n"

  if [ -f "$REPORTERS" ] && [ -f "$COMPLETED" ]; then
    reporter_progress=$(python3 - "$REPORTERS" "$COMPLETED" <<'PYEOF'
import json, sys
from collections import Counter
reporters = json.load(open(sys.argv[1], encoding="utf-8"))
completed = set()
with open(sys.argv[2], encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if line:
            completed.add(line)
total_by_site = Counter(r["site_name"] for r in reporters)
done_by_site = Counter(r["site_name"] for r in reporters
                        if f"{r['site']}:{r['reporter_id']}" in completed)
print(f" 記者進度         : {len(completed)}/{len(reporters)} 位完成")
for site in total_by_site:
    print(f"   {site:10s} : {done_by_site.get(site,0)}/{total_by_site[site]}")
PYEOF
)
    frame+="$reporter_progress\n"
  fi

  if [ "$prev_reporters" -gt 0 ] && [ "$reporter_delta" -ne 0 ]; then
    frame+="------------------------------------------------------\n"
    frame+=" 記者清單較上次刷新 : +$reporter_delta 位（探索仍在進行）\n"
  fi

  frame+="------------------------------------------------------\n"
  frame+=" 探索進度（記者搜尋，最後 6 行相關日誌）:\n"
  if [ -f "$LOG" ]; then
    recent_discover=$(grep -E "發現|即時新聞收集到|已造訪|共發現|完成：共|探索循環" "$LOG" 2>/dev/null | tail -n 6)
    if [ -n "$recent_discover" ]; then
      frame+="$(echo "$recent_discover" | sed 's/^/   /')\n"
    else
      frame+="   (目前無探索紀錄)\n"
    fi
  fi

  frame+="------------------------------------------------------\n"
  frame+=" 最近爬蟲日誌 (最後 8 行):\n"
  if [ -f "$LOG" ]; then
    recent_crawl=$(grep -E "子[0-9]+|強殺|穩健爬蟲啟動" "$LOG" 2>/dev/null | tail -n 8)
    frame+="$(echo "$recent_crawl" | sed 's/^/   /')\n"
    stall_count=$(grep -c "強殺" "$LOG" 2>/dev/null || echo 0)
    frame+="------------------------------------------------------\n"
    frame+=" 累計卡死重啟次數 : $stall_count\n"
  else
    frame+="   (找不到 log 檔：$LOG)\n"
  fi

  frame+="======================================================\n"
  frame+=" 每 ${INTERVAL} 秒自動刷新，Ctrl+C 結束\n"

  clear
  echo -e "$frame"

  prev_total=$total
  prev_reporters=$total_reporters
  prev_ts=$now_ts
  sleep "$INTERVAL"
done

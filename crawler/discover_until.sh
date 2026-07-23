#!/bin/bash
# 持續探索記者，直到清單達到指定人數，或連續 3 輪都沒有新增（視為飽和）才停止。
# 每輪沒有成長時會自動加深掃描範圍（更多頁數/滾動次數）再試一次。
#
# 用法：
#   ./discover_until.sh 500          # 目標 500 位記者
#   MAX_ROUNDS=30 ./discover_until.sh 500

set -u
cd "$(dirname "$0")/.."   # 專案根目錄

source ~/anaconda3/etc/profile.d/conda.sh
conda activate leo3.10

TARGET="${1:-500}"
MAX_ROUNDS="${MAX_ROUNDS:-20}"
REPORTERS="dataset/記者清單.json"
LOG="dataset/_state/crawl.log"

count() {
  python3 -c "import json; print(len(json.load(open('$REPORTERS', encoding='utf-8'))))" 2>/dev/null || echo 0
}

log() {
  echo "[$(date +%H:%M:%S)] [探索循環] $1" | tee -a "$LOG"
}

round=0
no_growth=0
ct_pages=60
ctee_clicks=100
udn_scrolls=40

log "開始持續探索，目標 $TARGET 位記者，目前 $(count) 位"

while [ "$(count)" -lt "$TARGET" ] && [ "$round" -lt "$MAX_ROUNDS" ]; do
  round=$((round + 1))
  before=$(count)
  log "第 $round 輪開始（CT_PAGES=$ct_pages CTEE_CLICKS=$ctee_clicks UDN_SCROLLS=$udn_scrolls），目前 $before 位"
  CT_PAGES=$ct_pages CTEE_CLICKS=$ctee_clicks UDN_SCROLLS=$udn_scrolls python3 crawler/discover.py
  after=$(count)
  log "第 $round 輪結束：$before -> $after 位（目標 $TARGET）"

  if [ "$after" -le "$before" ]; then
    no_growth=$((no_growth + 1))
    ct_pages=$((ct_pages + 40))
    ctee_clicks=$((ctee_clicks + 60))
    udn_scrolls=$((udn_scrolls + 20))
    if [ "$no_growth" -ge 3 ]; then
      log "連續 3 輪沒有新記者，可能已接近飽和，停止。目前 $after 位（目標 $TARGET）"
      exit 0
    fi
  else
    no_growth=0
  fi
done

final=$(count)
if [ "$final" -ge "$TARGET" ]; then
  log "已達目標：$final 位（目標 $TARGET），結束探索循環"
else
  log "達最大輪數（$MAX_ROUNDS），未達目標：$final 位（目標 $TARGET）"
fi

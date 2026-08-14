#!/bin/zsh
# รัน so_push ทั้ง 3 สาขา — อ่าน DBF จาก Drive mount -> push Supabase กลาง
DIR="$(cd "$(dirname "$0")" && pwd)"
source "$DIR/feeder.env"
STDIR="$HOME/007so_push"; mkdir -p "$STDIR"
LOG="$STDIR/feeder.log"
echo "===== $(date '+%Y-%m-%d %H:%M:%S') START =====" >> "$LOG"
if [ -z "$PROXY_URL" ] && [ ! -d "$DRIVE_ROOT" ]; then
  echo "  ERROR: ไม่พบ DRIVE_ROOT = $DRIVE_ROOT (ยังไม่ mount/ตั้ง offline?)" >> "$LOG"; exit 1
fi
for b in SKN BK PPS; do
  cf="$STDIR/cfg_${b}.txt"
  if [ -n "$PROXY_URL" ]; then
    # Drive API ผ่าน Apps Script proxy — เลี่ยง local FileProvider mount ที่ launchd/cron อ่านไม่ได้
    printf 'BRANCH=%s\nPROXY_URL=%s\nPROXY_TOKEN=%s\nSUPABASE_URL=%s\nSUPABASE_KEY=%s\nWINDOW_DAYS=%s\n' \
      "$b" "$PROXY_URL" "$PROXY_TOKEN" "$SUPABASE_URL" "$SUPABASE_KEY" "$WINDOW_DAYS" > "$cf"
  else
    printf 'BRANCH=%s\nSRC=%s/%s\nSUPABASE_URL=%s\nSUPABASE_KEY=%s\nWINDOW_DAYS=%s\n' \
      "$b" "$DRIVE_ROOT" "$b" "$SUPABASE_URL" "$SUPABASE_KEY" "$WINDOW_DAYS" > "$cf"
  fi
  /usr/bin/python3 "$DIR/so_push.py" "$cf" >> "$LOG" 2>&1
  /usr/bin/python3 "$DIR/sopo_month.py" "$cf" >> "$LOG" 2>&1
  /usr/bin/python3 "$DIR/dead_stock.py" "$cf" >> "$LOG" 2>&1
  # บิลขาย Express (ARTRN) -> express_bill ของ quote-app — ตัวสคริปต์คุมจังหวะเอง
  # ชั่วโมงละครั้ง (EXPRESS_EVERY_MIN) ส่ง service_role key ทาง env ไม่เขียนลง cfg
  # เพราะ express_bill เขียนได้เฉพาะ service role (ดู quote_schema.sql)
  SUPABASE_SERVICE_KEY="$SUPABASE_SERVICE_KEY" EXPRESS_EVERY_MIN="$EXPRESS_EVERY_MIN" \
    /usr/bin/python3 "$DIR/express_sync.py" "$cf" >> "$LOG" 2>&1
done
# Finny (report_scores.csv) — ไฟล์เดียวใช้ร่วมทุกสาขา รันครั้งเดียวต่อรอบ (ใช้ cfg ล่าสุดจาก loop บน)
/usr/bin/python3 "$DIR/finny_sync.py" "$cf" >> "$LOG" 2>&1
echo "$(date '+%Y-%m-%d %H:%M:%S') DONE" >> "$LOG"

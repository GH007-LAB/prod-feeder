#!/bin/zsh
# รัน so_push ทั้ง 3 สาขา — อ่าน DBF จาก Drive mount -> push Supabase กลาง
DIR="$(cd "$(dirname "$0")" && pwd)"
source "$DIR/feeder.env"
STDIR="$HOME/007so_push"; mkdir -p "$STDIR"
LOG="$STDIR/feeder.log"
echo "===== $(date '+%Y-%m-%d %H:%M:%S') START =====" >> "$LOG"
if [ ! -d "$DRIVE_ROOT" ]; then
  echo "  ERROR: ไม่พบ DRIVE_ROOT = $DRIVE_ROOT (ยังไม่ mount/ตั้ง offline?)" >> "$LOG"; exit 1
fi
for b in SKN BK PPS; do
  cf="$STDIR/cfg_${b}.txt"
  printf 'BRANCH=%s\nSRC=%s/%s\nSUPABASE_URL=%s\nSUPABASE_KEY=%s\nWINDOW_DAYS=%s\n' \
    "$b" "$DRIVE_ROOT" "$b" "$SUPABASE_URL" "$SUPABASE_KEY" "$WINDOW_DAYS" > "$cf"
  /usr/bin/python3 "$DIR/so_push.py" "$cf" >> "$LOG" 2>&1
done
echo "$(date '+%Y-%m-%d %H:%M:%S') DONE" >> "$LOG"

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
# DBF ในเครื่อง (Drive Mirror ของ 007skn0777 — ไฟล์จริง launchd อ่านได้ ต่างจาก
# FileProvider สมัยก่อนที่ต้องอ้อม proxy) — 3 ก.ย. 69 CTO ให้เร่งรอบอัพเดทระบบผลิต:
# proxy โหลด DBF ~3.4 นาที/สาขา = รอบละ ~11 นาที (จริงห่าง 21-23 นาที เพราะเกิน
# StartInterval 600) → อ่าน local เหลือไม่กี่วินาที · ถ้า mirror หาย/ค้างเกิน 3 ชม.
# ถอยไป proxy อัตโนมัติ (Drive app ตาย ระบบต้องไม่หยุด)
LOCAL_AE="$HOME/ไดรฟ์ของฉัน (007skn0777@gmail.com)/All_on_Cloud/AutoExport"
for b in SKN BK PPS; do
  cf="$STDIR/cfg_${b}.txt"
  use_local=""
  if [ -f "$LOCAL_AE/$b/ARTRN.DBF" ]; then
    age=$(( $(date +%s) - $(stat -f %m "$LOCAL_AE/$b/ARTRN.DBF") ))
    # mirror ใช้ได้เมื่อ: ไฟล์สด <3 ชม. หรือแอป Google Drive ยังรันอยู่
    # (5 ก.ย. 69: กลางคืนสาขาปิด ไฟล์ไม่ขยับเป็นธรรมดา — เงื่อนไขอายุอย่างเดียว
    #  ทำให้หลงถอยไป proxy ช้า 10-13 นาที/รอบทั้งคืนโดยไม่จำเป็น)
    if [ "$age" -lt 10800 ] || pgrep -xq "Google Drive"; then use_local=1; fi
  fi
  if [ -n "$use_local" ]; then
    # SUPABASE_KEY ใน cfg = service role (20 ส.ค. 69 — audit ความปลอดภัย เหมือนเดิม)
    printf 'BRANCH=%s\nSRC=%s/%s\nSUPABASE_URL=%s\nSUPABASE_KEY=%s\nWINDOW_DAYS=%s\n' \
      "$b" "$LOCAL_AE" "$b" "$SUPABASE_URL" "$SUPABASE_SERVICE_KEY" "$WINDOW_DAYS" > "$cf"
  elif [ -n "$PROXY_URL" ]; then
    echo "  $b: mirror ไม่พร้อม (อายุเกิน 3 ชม./ไม่มีไฟล์) -> ใช้ proxy" >> "$LOG"
    printf 'BRANCH=%s\nPROXY_URL=%s\nPROXY_TOKEN=%s\nSUPABASE_URL=%s\nSUPABASE_KEY=%s\nWINDOW_DAYS=%s\n' \
      "$b" "$PROXY_URL" "$PROXY_TOKEN" "$SUPABASE_URL" "$SUPABASE_SERVICE_KEY" "$WINDOW_DAYS" > "$cf"
  else
    printf 'BRANCH=%s\nSRC=%s/%s\nSUPABASE_URL=%s\nSUPABASE_KEY=%s\nWINDOW_DAYS=%s\n' \
      "$b" "$DRIVE_ROOT" "$b" "$SUPABASE_URL" "$SUPABASE_SERVICE_KEY" "$WINDOW_DAYS" > "$cf"
  fi
  /usr/bin/python3 "$DIR/so_push.py" "$cf" >> "$LOG" 2>&1
  /usr/bin/python3 "$DIR/sopo_month.py" "$cf" >> "$LOG" 2>&1
  /usr/bin/python3 "$DIR/dead_stock.py" "$cf" >> "$LOG" 2>&1
  # บิลขาย Express (ARTRN) -> express_bill ของ quote-app — ตัวสคริปต์คุมจังหวะเอง
  # ชั่วโมงละครั้ง (EXPRESS_EVERY_MIN) ส่ง service_role key ทาง env ไม่เขียนลง cfg
  # เพราะ express_bill เขียนได้เฉพาะ service role (ดู quote_schema.sql)
  SUPABASE_SERVICE_KEY="$SUPABASE_SERVICE_KEY" EXPRESS_EVERY_MIN="$EXPRESS_EVERY_MIN" \
    /usr/bin/python3 "$DIR/express_sync.py" "$cf" >> "$LOG" 2>&1
  # ตรวจว่าตัวเลขใน Supabase ตรงกับ DBF จริงไหม (ชั่วโมงละครั้งเหมือนกัน)
  # ไม่ผ่านเมื่อไหร่ขึ้นบรรทัด VERIFY-FAIL: grep 'VERIFY-FAIL' ~/007so_push/feeder.log
  SUPABASE_SERVICE_KEY="$SUPABASE_SERVICE_KEY" VERIFY_EVERY_MIN="$VERIFY_EVERY_MIN" \
    /usr/bin/python3 "$DIR/verify.py" "$cf" >> "$LOG" 2>&1
done
# Finny (report_scores.csv) — ไฟล์เดียวใช้ร่วมทุกสาขา รันครั้งเดียวต่อรอบ
# ต้องใช้ proxy เสมอ (fileId mode ไม่รองรับ local) — cfg สาขาตอนนี้อาจเป็น SRC local
# จึงเขียน cfg แยกให้ finny โดยเฉพาะ
cf_finny="$STDIR/cfg_finny.txt"
printf 'BRANCH=SKN\nPROXY_URL=%s\nPROXY_TOKEN=%s\nSUPABASE_URL=%s\nSUPABASE_KEY=%s\n' \
  "$PROXY_URL" "$PROXY_TOKEN" "$SUPABASE_URL" "$SUPABASE_SERVICE_KEY" > "$cf_finny"
/usr/bin/python3 "$DIR/finny_sync.py" "$cf_finny" >> "$LOG" 2>&1
# action log จากจอ SOPO เดิม (Apps Script) -> sopo_action — ไฟล์เดียวรวมทุกสาขา
# รันครั้งเดียวต่อรอบ ตัวสคริปต์คุมจังหวะเองชั่วโมงละครั้ง (ALOG_EVERY_MIN)
SUPABASE_SERVICE_KEY="$SUPABASE_SERVICE_KEY" ALOG_URL="$ALOG_URL" ALOG_EVERY_MIN="$ALOG_EVERY_MIN" \
  /usr/bin/python3 "$DIR/alog_sync.py" "$cf" >> "$LOG" 2>&1
# คะแนนพัฒนาการรายเดือน -> sopo_dev_month (HR Merit อ่านผ่าน view hr_dev_score)
# อ่านจาก Supabase ล้วน เร็วมาก รันทุกรอบได้
/usr/bin/python3 "$DIR/dev_score.py" "$cf" >> "$LOG" 2>&1
# รายการงานให้กด ✔ (sopo_item) — สร้างจาก DBF ตรง แทน sync จากเครื่อง Windows สกลนคร
# ชั่วโมงละครั้ง (QUESTS_EVERY_MIN) ต้องใช้ service key
SUPABASE_SERVICE_KEY="$SUPABASE_SERVICE_KEY" QUESTS_EVERY_MIN="$QUESTS_EVERY_MIN" \
  /usr/bin/python3 "$DIR/quests_sync.py" "$cf" >> "$LOG" 2>&1
# ตรวจสิทธิ์การเห็นข้อมูล (RLS) ด้วยตัวตน anon + auth-unlinked — ชั่วโมงละครั้ง
# หลุดข้อไหนขึ้น SMOKE-FAIL (grep 'SMOKE-FAIL' ~/007so_push/feeder.log)
SUPABASE_URL="$SUPABASE_URL" SUPABASE_ANON_KEY="$SUPABASE_ANON_KEY" \
  SUPABASE_SERVICE_KEY="$SUPABASE_SERVICE_KEY" SMOKE_EMAIL="$SMOKE_EMAIL" \
  SMOKE_PASSWORD="$SMOKE_PASSWORD" SMOKE_EVERY_MIN="$SMOKE_EVERY_MIN" \
  /usr/bin/python3 "$DIR/smoke_rls.py" >> "$LOG" 2>&1
# สำรองไฟล์ Storage (รูปกิโล/หน้างาน/พนักงาน) ลงเครื่อง — วันละครั้ง (backup Supabase ไม่รวม Storage)
SUPABASE_URL="$SUPABASE_URL" SUPABASE_SERVICE_KEY="$SUPABASE_SERVICE_KEY" \
  STORAGE_BACKUP_EVERY_MIN="$STORAGE_BACKUP_EVERY_MIN" \
  /usr/bin/python3 "$DIR/storage_backup.py" >> "$LOG" 2>&1
# สั่งรอบเก็บแดชบอร์ดยอดขาย (SP-TT-dashboard collect.yml) ชั่วโมงละครั้ง
# เหตุ (2 ก.ย. 69): cron รายชั่วโมงบน GitHub ถูกข้ามรอบ ~81% (จริงเฉลี่ยทุก 5.3 ชม.
# แย่สุด 13 ชม.) ทำให้ยอดวันนี้บนแดชบอร์ดค้าง — ให้เครื่องเรากดผ่าน workflow_dispatch
# เอง (cron ของ GitHub คงไว้เป็นชั้นสำรอง · concurrency ใน workflow กันรันซ้อนแล้ว)
# พักการ dispatch ชั่วคราวเมื่อมีไฟล์ gh_dispatch_pause (12 ก.ย. 69: GitHub Actions
# ติดบิลลิ่ง ทุก run ล้ม — ยิงไปก็สร้าง run failed เปล่า ๆ + สแปมเมลแจ้งเตือน)
if [ ! -f "$STDIR/gh_dispatch_pause" ]; then
COLLECT_STAMP="$STDIR/collect_dispatch.stamp"
if [ ! -f "$COLLECT_STAMP" ] || [ $(( $(date +%s) - $(stat -f %m "$COLLECT_STAMP") )) -ge 3300 ]; then
  if /Users/cto007/bin/gh workflow run collect.yml -R GH007-LAB/SP-TT-dashboard >> "$LOG" 2>&1; then
    touch "$COLLECT_STAMP"
    echo "$(date '+%Y-%m-%d %H:%M:%S') COLLECT-DISPATCH: สั่งรอบเก็บแดชบอร์ดแล้ว" >> "$LOG"
  else
    # ใช้คำว่า ERROR ให้ alert_scan จับไปแจ้ง LINE (เช่น gh token หมดอายุ/keychain ล็อก)
    echo "$(date '+%Y-%m-%d %H:%M:%S') ERROR: COLLECT-DISPATCH สั่งรอบเก็บแดชบอร์ดไม่สำเร็จ" >> "$LOG"
  fi
fi
# รอบเก็บ TikTok ตัวเบา (ออเดอร์ 1 วัน) ทุก ~10 นาที — เร่งความสดให้ฟีเจอร์
# ประทับใบ label (CTO เคาะ 8 ก.ย. 69: ออเดอร์ใหม่ต้องเข้า DB ใน ~10 นาที)
# full collect รายชั่วโมงยังอยู่ (backfill 3 วัน + แพลตฟอร์มอื่น)
TTQ_STAMP="$STDIR/tiktok_quick.stamp"
if [ ! -f "$TTQ_STAMP" ] || [ $(( $(date +%s) - $(stat -f %m "$TTQ_STAMP") )) -ge 570 ]; then
  if /Users/cto007/bin/gh workflow run tiktok_quick.yml -R GH007-LAB/SP-TT-dashboard >> "$LOG" 2>&1; then
    touch "$TTQ_STAMP"
    echo "$(date '+%Y-%m-%d %H:%M:%S') TIKTOK-QUICK: สั่งรอบเก็บ TikTok เร็วแล้ว" >> "$LOG"
  else
    echo "$(date '+%Y-%m-%d %H:%M:%S') ERROR: TIKTOK-QUICK สั่งรอบเก็บไม่สำเร็จ" >> "$LOG"
  fi
fi

# สรุปสุขภาพร้านเข้า LINE ทีมผลิต-จัดส่ง (CTO 14 ก.ย. 69) — สั่งวันละครั้งตอน 09:xx ไทย
# health_alert ใช้ ref ถัง 2 วัน = ได้ "วันเว้นวัน" เอง · เขียวหมดไม่ส่ง
# stamp 20 ชม. กันยิงซ้ำในวันเดียวกัน (feeder เดินทุก 5 นาที)
HEALTH_STAMP="$STDIR/health_digest.stamp"
if [ "$(date +%H)" = "09" ] && { [ ! -f "$HEALTH_STAMP" ] || \
   [ $(( $(date +%s) - $(stat -f %m "$HEALTH_STAMP") )) -ge 72000 ]; }; then
  if /Users/cto007/bin/gh workflow run health_digest.yml -R GH007-LAB/SP-TT-dashboard >> "$LOG" 2>&1; then
    touch "$HEALTH_STAMP"
    echo "$(date '+%Y-%m-%d %H:%M:%S') HEALTH-DIGEST: สั่งสรุปสุขภาพร้านแล้ว" >> "$LOG"
  else
    echo "$(date '+%Y-%m-%d %H:%M:%S') ERROR: HEALTH-DIGEST สั่งสรุปสุขภาพร้านไม่สำเร็จ" >> "$LOG"
  fi
fi
fi  # gh_dispatch_pause

# Watchdog runner ค้าง (13 ก.ย. 69: Runner.Listener ซอมบี้ — โชว์ online แต่ไม่หยิบงาน
# run ค้าง pending 1.5 ชม. จน LINE เตือนรัว) — ถ้ามี run ค้างคิวเกิน 12 นาที
# ให้ restart service (จำกัดไม่เกิน 1 ครั้ง/30 นาที กัน restart วน)
RUNNER_KICK_STAMP="$STDIR/runner_kick.stamp"
if [ ! -f "$RUNNER_KICK_STAMP" ] || [ $(( $(date +%s) - $(stat -f %m "$RUNNER_KICK_STAMP") )) -ge 1800 ]; then
  OLDEST_QUEUED=$(/Users/cto007/bin/gh run list -R GH007-LAB/SP-TT-dashboard --limit 10     --json status,createdAt --jq '[.[] | select(.status=="queued" or .status=="pending")] | sort_by(.createdAt) | .[0].createdAt // empty' 2>/dev/null)
  # kick เฉพาะตอน runner "ว่าง" เท่านั้น — คิวค้างระหว่างมี job รันอยู่เป็นเรื่องปกติ
  # (บทเรียน 13 ก.ย. 69: เวอร์ชันแรกไม่เช็ค busy เลยฆ่างานกลางคันจน broker session ชน)
  RUNNER_BUSY=$(/Users/cto007/bin/gh api repos/GH007-LAB/SP-TT-dashboard/actions/runners --jq '.runners[] | select(.name=="macmini-cto") | .busy' 2>/dev/null)
  if [ -n "$OLDEST_QUEUED" ] && [ "$RUNNER_BUSY" = "false" ]; then
    QUEUED_AGE=$(( $(date +%s) - $(date -j -u -f "%Y-%m-%dT%H:%M:%SZ" "$OLDEST_QUEUED" +%s 2>/dev/null || date +%s) ))
    if [ "$QUEUED_AGE" -ge 720 ]; then
      launchctl kickstart -k "gui/$(id -u)/actions.runner.GH007-LAB-SP-TT-dashboard.macmini-cto" 2>> "$LOG"
      touch "$RUNNER_KICK_STAMP"
      echo "$(date '+%Y-%m-%d %H:%M:%S') RUNNER-KICK: run ค้างคิว $((QUEUED_AGE/60)) นาที -> restart runner service แล้ว" >> "$LOG"
    fi
  fi
fi

# แจ้งเตือนความผิดปกติเข้า LINE: สแกน log รอบนี้ + เรียก dispatcher บน hr-app
# (VERIFY-FAIL / SMOKE-FAIL / backup พัง / ข้อมูลค้าง / ยอดเงินเข้าไม่ตรง)
SUPABASE_URL="$SUPABASE_URL" SUPABASE_SERVICE_KEY="$SUPABASE_SERVICE_KEY" \
  ALERTS_URL="$ALERTS_URL" \
  /usr/bin/python3 "$DIR/alert_scan.py" >> "$LOG" 2>&1
echo "$(date '+%Y-%m-%d %H:%M:%S') DONE" >> "$LOG"

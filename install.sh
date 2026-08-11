#!/bin/zsh
# ติดตั้ง feeder เป็น launchd (รันทุก 10 นาที) บนเครื่องนี้
DIR="$(cd "$(dirname "$0")" && pwd)"
source "$DIR/feeder.env"
if [[ "$DRIVE_ROOT" == /CHANGE/* ]]; then
  echo "❌ ยังไม่ได้ตั้ง DRIVE_ROOT ใน feeder.env — แก้ก่อน"; exit 1
fi
if [ ! -d "$DRIVE_ROOT" ]; then
  echo "❌ ไม่พบโฟลเดอร์: $DRIVE_ROOT"; echo "   (ยัง Add shortcut to Drive + ตั้ง offline หรือยัง?)"; exit 1
fi
if [ ! -f "$DRIVE_ROOT/SKN/OESO.DBF" ]; then
  echo "⚠️  ไม่พบ $DRIVE_ROOT/SKN/OESO.DBF — เช็ค path/offline ก่อน"; exit 1
fi
PLIST="$HOME/Library/LaunchAgents/com.007metals.prodfeeder.plist"
mkdir -p "$HOME/Library/LaunchAgents" "$HOME/007so_push"
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.007metals.prodfeeder</string>
  <key>ProgramArguments</key><array><string>/bin/zsh</string><string>$DIR/run_all.sh</string></array>
  <key>StartInterval</key><integer>600</integer>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>$HOME/007so_push/launchd.out</string>
  <key>StandardErrorPath</key><string>$HOME/007so_push/launchd.err</string>
</dict></plist>
EOF
launchctl unload "$PLIST" 2>/dev/null
launchctl load "$PLIST"
echo "✅ ติดตั้ง + โหลดแล้ว (รันทุก 10 นาที) — log: ~/007so_push/feeder.log"
echo "   ทดสอบมือ 1 รอบ: $DIR/run_all.sh"

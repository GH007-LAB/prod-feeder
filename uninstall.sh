#!/bin/zsh
PLIST="$HOME/Library/LaunchAgents/com.007metals.prodfeeder.plist"
launchctl unload "$PLIST" 2>/dev/null && rm -f "$PLIST" && echo "✅ ปิด feeder แล้ว"

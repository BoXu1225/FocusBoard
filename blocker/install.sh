#!/bin/sh
# Install (or update) the FocusBoard blocker. Run from the repo root:
#   sudo ./blocker/install.sh            # uses ./blocklist, else blocklist.example
#   sudo ./blocker/install.sh --uninstall
# Copies block.py and the blocklist to root-owned locations and runs the
# blocker every minute: a LaunchDaemon on macOS, /etc/cron.d on Linux.
set -eu

LIB=/usr/local/lib/focusboard
ETC=/usr/local/etc/focusboard
BIN=/usr/local/bin/focusblock
PLIST=/Library/LaunchDaemons/com.focusboard.blocker.plist
CRON=/etc/cron.d/focusboard
PY=/usr/bin/python3

[ "$(id -u)" = 0 ] || { echo "run with sudo" >&2; exit 1; }
cd "$(dirname "$0")/.."

if [ "${1:-}" = "--uninstall" ]; then
  [ -f "$LIB/block.py" ] && "$PY" "$LIB/block.py" off --config "$ETC/blocklist" || true
  if [ "$(uname)" = Darwin ]; then
    launchctl bootout system "$PLIST" 2>/dev/null || true
    rm -f "$PLIST"
  else
    rm -f "$CRON"
  fi
  rm -f "$BIN" "$LIB/block.py" "$ETC/blocklist" "$ETC/pause_until"
  rmdir "$LIB" "$ETC" 2>/dev/null || true
  echo "FocusBoard blocker removed"
  exit 0
fi

SRC=blocklist; [ -f "$SRC" ] || SRC=blocklist.example
"$PY" blocker/block.py status --config "$SRC" --hosts /etc/hosts >/dev/null  # validates the list

mkdir -p "$LIB" "$ETC"
install -m 755 blocker/block.py "$LIB/block.py"
install -m 644 "$SRC" "$ETC/blocklist"
chown -R root "$LIB" "$ETC"; chmod 755 "$LIB" "$ETC"

cat > "$BIN" <<EOS
#!/bin/sh
# focusblock status | pause MIN | resume | off   (pause/resume/off need sudo)
exec $PY $LIB/block.py "\$@" --config $ETC/blocklist
EOS
chmod 755 "$BIN"

if [ "$(uname)" = Darwin ]; then
  cat > "$PLIST" <<EOS
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.focusboard.blocker</string>
  <key>ProgramArguments</key><array>
    <string>$PY</string><string>$LIB/block.py</string><string>apply</string>
    <string>--config</string><string>$ETC/blocklist</string>
  </array>
  <key>StartInterval</key><integer>60</integer>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>/var/log/focusboard-blocker.log</string>
  <key>StandardErrorPath</key><string>/var/log/focusboard-blocker.log</string>
</dict></plist>
EOS
  chmod 644 "$PLIST"
  launchctl bootout system "$PLIST" 2>/dev/null || true
  launchctl bootstrap system "$PLIST"
else
  echo "* * * * * root $PY $LIB/block.py apply --config $ETC/blocklist >> /var/log/focusboard-blocker.log 2>&1" > "$CRON"
  chmod 644 "$CRON"
fi

"$PY" "$LIB/block.py" apply --config "$ETC/blocklist" || true
"$PY" "$LIB/block.py" status --config "$ETC/blocklist"
echo "installed. Manage with: focusblock status | sudo focusblock pause 15 | sudo focusblock resume"

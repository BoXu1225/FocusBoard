#!/bin/sh
# Run the activity sampler at login, as your user (no sudo).
#   ./sampler/install.sh http://<server>:5070      # install / update
#   ./sampler/install.sh --uninstall
set -eu
cd "$(dirname "$0")/.."
REPO="$(pwd)"
PLIST="$HOME/Library/LaunchAgents/com.focusboard.sampler.plist"
DESKTOP="$HOME/.config/autostart/focusboard-sampler.desktop"

if [ "${1:-}" = "--uninstall" ]; then
  if [ "$(uname)" = Darwin ]; then
    launchctl bootout "gui/$(id -u)" "$PLIST" 2>/dev/null || true; rm -f "$PLIST"
  else
    rm -f "$DESKTOP"; pkill -f "sampler/sampler.py" || true
  fi
  echo "sampler removed"; exit 0
fi

URL="${1:?usage: sampler/install.sh http://<server>:5070}"
if [ "$(uname)" = Darwin ]; then
  mkdir -p "$(dirname "$PLIST")"
  cat > "$PLIST" <<EOS
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.focusboard.sampler</string>
  <key>ProgramArguments</key><array>
    <string>/usr/bin/python3</string><string>$REPO/sampler/sampler.py</string>
    <string>--server</string><string>$URL</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardErrorPath</key><string>$REPO/sampler.log</string>
</dict></plist>
EOS
  launchctl bootout "gui/$(id -u)" "$PLIST" 2>/dev/null || true
  launchctl bootstrap "gui/$(id -u)" "$PLIST"
else
  mkdir -p "$(dirname "$DESKTOP")"
  cat > "$DESKTOP" <<EOS
[Desktop Entry]
Type=Application
Name=FocusBoard sampler
Exec=python3 $REPO/sampler/sampler.py --server $URL
X-GNOME-Autostart-enabled=true
NoDisplay=true
EOS
  echo "starts at your next desktop login (needs an X11 session; xdotool, xprop, optionally xprintidle)"
fi
echo "sampler installed -> $URL"

#!/bin/sh
# Start the FocusBoard server. By default it binds to this machine's Tailscale
# IP (so your other devices can reach it, but not the LAN), else 127.0.0.1.
# Override with FOCUSBOARD_HOST / FOCUSBOARD_PORT / FOCUSBOARD_DB.
cd "$(dirname "$0")" || exit 1
if [ -z "${FOCUSBOARD_HOST:-}" ]; then
  IP=""
  i=0
  while [ $i -lt 60 ]; do   # wait for Tailscale at boot (up to ~5 min)
    IP="$(tailscale ip -4 2>/dev/null | head -1)"
    [ -n "$IP" ] && break
    command -v tailscale >/dev/null 2>&1 || break
    i=$((i + 1)); sleep 5
  done
  FOCUSBOARD_HOST="${IP:-127.0.0.1}"
fi
export FOCUSBOARD_HOST
exec python3 focusboard/server.py

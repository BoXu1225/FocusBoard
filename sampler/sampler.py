#!/usr/bin/env python3
"""FocusBoard activity sampler: once a minute, record the foreground app.

Posts {host, ts, app, title, idle_s} to the FocusBoard server so the Timeline
page shows what you actually did. Runs as your user, in your desktop session.

    sampler.py --server http://127.0.0.1:5070 [--interval 60] [--no-titles]

macOS: app name via `lsappinfo` and idle time via `ioreg` (no permissions
needed; window titles aren't read). Linux/X11: `xdotool` + `xprop` for the
active window's app and title, `xprintidle` for idle time if installed.
Samples are buffered while the server is unreachable and sent later.
Python 3.9+, standard library only.
"""
import argparse
import datetime as dt
import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.request


def run(cmd):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=5).stdout.strip()
    except Exception:
        return ""


def mac_sample():
    front = run(["lsappinfo", "front"])
    app = ""
    if front:
        m = re.search(r'"LSDisplayName"\s*=\s*"([^"]*)"', run(["lsappinfo", "info", "-only", "name", front]))
        app = m.group(1) if m else ""
    idle = 0
    m = re.search(r'"HIDIdleTime"\s*=\s*(\d+)', run(["ioreg", "-c", "IOHIDSystem", "-d", "4"]))
    if m:
        idle = int(m.group(1)) // 1_000_000_000
    return app, "", idle


def linux_sample(titles):
    env_ok = os.environ.get("DISPLAY")
    app = title = ""
    if env_ok:
        wid = run(["xdotool", "getactivewindow"])
        if wid:
            cls = run(["xprop", "-id", wid, "WM_CLASS"])
            m = re.findall(r'"([^"]*)"', cls)
            app = m[-1] if m else ""
            if titles:
                title = run(["xdotool", "getwindowname", wid])
    idle = 0
    out = run(["xprintidle"])
    if out.isdigit():
        idle = int(out) // 1000
    return app, title, idle


def sample(titles):
    if sys.platform == "darwin":
        return mac_sample()
    return linux_sample(titles)


def post(server, items):
    req = urllib.request.Request(server.rstrip("/") + "/api/samples",
                                 data=json.dumps(items).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return r.status == 200


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--server", default=os.environ.get("FOCUSBOARD_URL", "http://127.0.0.1:5070"))
    p.add_argument("--interval", type=int, default=60)
    p.add_argument("--host", default=socket.gethostname().split(".")[0])
    p.add_argument("--no-titles", action="store_true", help="don't record window titles")
    p.add_argument("--once", action="store_true", help="take one sample, print it, exit")
    a = p.parse_args()

    buf = []
    while True:
        app, title, idle = sample(not a.no_titles)
        item = {"host": a.host, "ts": dt.datetime.now().isoformat(timespec="seconds"),
                "app": app, "title": title, "idle_s": idle}
        if a.once:
            print(json.dumps(item))
            return
        buf.append(item)
        buf = buf[-1440:]  # keep at most a day while offline
        try:
            if post(a.server, buf):
                buf = []
        except Exception:
            pass
        time.sleep(a.interval)


if __name__ == "__main__":
    main()

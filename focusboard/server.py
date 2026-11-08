#!/usr/bin/env python3
"""FocusBoard web app: today's plan, todos, a "now doing" timer and a timeline.

Standard library only (Python 3.9+). One SQLite file. Serves a single page
plus a small JSON API that the page and the activity sampler use.

    FOCUSBOARD_HOST (default 127.0.0.1)   FOCUSBOARD_PORT (default 5070)
    FOCUSBOARD_DB   (default ./focusboard.db next to the repo root)
"""
import datetime as dt
import json
import os
import re
import sqlite3
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DB_PATH = os.environ.get("FOCUSBOARD_DB", os.path.join(ROOT, "focusboard.db"))
HOSTS = "/etc/hosts"
BLOCK_MARK = "# >>> focusboard >>>"

SCHEMA = """
CREATE TABLE IF NOT EXISTS blocks (      -- planned time blocks
  id INTEGER PRIMARY KEY, day TEXT NOT NULL, start TEXT NOT NULL, end TEXT NOT NULL,
  title TEXT NOT NULL, first_step TEXT DEFAULT '', created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS tasks (       -- todos; day NULL = backlog
  id INTEGER PRIMARY KEY, title TEXT NOT NULL, day TEXT, done_at TEXT,
  created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sessions (    -- what I was doing (timer)
  id INTEGER PRIMARY KEY, label TEXT NOT NULL, task_id INTEGER,
  start_ts TEXT NOT NULL, end_ts TEXT);
CREATE TABLE IF NOT EXISTS samples (     -- foreground app, from sampler.py
  id INTEGER PRIMARY KEY, host TEXT NOT NULL, ts TEXT NOT NULL, app TEXT,
  title TEXT, idle_s INTEGER);
CREATE INDEX IF NOT EXISTS samples_ts ON samples(ts);
CREATE INDEX IF NOT EXISTS sessions_start ON sessions(start_ts);
"""

_lock = threading.Lock()


def now():
    return dt.datetime.now().replace(microsecond=0)


def iso(t):
    return t.isoformat(timespec="seconds")


def db():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def init_db():
    with db() as c:
        c.executescript(SCHEMA)


def rows(cur):
    return [dict(r) for r in cur.fetchall()]


DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
HM_RE = re.compile(r"^\d{2}:\d{2}$")


def valid_day(s):
    if not s or not DAY_RE.match(s):
        raise ValueError("day must be YYYY-MM-DD")
    dt.date.fromisoformat(s)
    return s


def text(s, field, maxlen=300, required=True):
    s = (s or "").strip()
    if required and not s:
        raise ValueError("%s is required" % field)
    return s[:maxlen]


def blocker_active():
    try:
        with open(HOSTS) as f:
            return BLOCK_MARK in f.read()
    except OSError:
        return None


# ---------------------------------------------------------------- API logic

def get_day(day):
    with db() as c:
        blocks = rows(c.execute("SELECT * FROM blocks WHERE day=? ORDER BY start", (day,)))
        tasks = rows(c.execute(
            "SELECT * FROM tasks WHERE day=? OR (day IS NULL AND done_at IS NULL) "
            "OR (day < ? AND done_at IS NULL) ORDER BY done_at IS NOT NULL, day IS NULL, id",
            (day, day)))
        sessions = rows(c.execute(
            "SELECT * FROM sessions WHERE substr(start_ts,1,10)=? OR end_ts IS NULL ORDER BY start_ts",
            (day,)))
        running = rows(c.execute("SELECT * FROM sessions WHERE end_ts IS NULL"))
    return {"day": day, "blocks": blocks, "tasks": tasks, "sessions": sessions,
            "running": running[0] if running else None, "blocker": blocker_active(),
            "now": iso(now())}


def timeline(day, bucket_min=10):
    """Per-bucket summary of the sampler data: top app per host, idle marked."""
    with db() as c:
        samples = rows(c.execute(
            "SELECT host, ts, app, title, idle_s FROM samples WHERE substr(ts,1,10)=? ORDER BY ts",
            (day,)))
    buckets = {}
    for s in samples:
        t = dt.datetime.fromisoformat(s["ts"])
        key = (s["host"], t.hour * 60 + (t.minute // bucket_min) * bucket_min)
        app = "idle" if (s["idle_s"] or 0) >= 300 else (s["app"] or "?")
        buckets.setdefault(key, {}).setdefault(app, 0)
        buckets[key][app] += 1
    out = []
    for (host, minute), apps in sorted(buckets.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        top = max(apps.items(), key=lambda kv: kv[1])[0]
        out.append({"host": host, "start": "%02d:%02d" % divmod(minute, 60),
                    "app": top, "mix": apps})
    # app totals in minutes (one sample ~ one minute)
    totals = {}
    for s in samples:
        app = "idle" if (s["idle_s"] or 0) >= 300 else (s["app"] or "?")
        totals[app] = totals.get(app, 0) + 1
    return {"day": day, "bucket_min": bucket_min, "buckets": out,
            "totals": sorted(totals.items(), key=lambda kv: -kv[1])}


def week(day):
    d = dt.date.fromisoformat(day)
    start = d - dt.timedelta(days=d.weekday())
    days = [(start + dt.timedelta(days=i)).isoformat() for i in range(7)]
    out = []
    with db() as c:
        for dd in days:
            sess = rows(c.execute(
                "SELECT label, start_ts, COALESCE(end_ts, ?) AS end_ts FROM sessions "
                "WHERE substr(start_ts,1,10)=?", (iso(now()), dd)))
            by = {}
            for s in sess:
                mins = (dt.datetime.fromisoformat(s["end_ts"]) -
                        dt.datetime.fromisoformat(s["start_ts"])).total_seconds() / 60
                by[s["label"]] = by.get(s["label"], 0) + mins
            planned = 0
            for b in rows(c.execute("SELECT start, end FROM blocks WHERE day=?", (dd,))):
                h1, m1 = map(int, b["start"].split(":"))
                h2, m2 = map(int, b["end"].split(":"))
                planned += max(0, (h2 * 60 + m2) - (h1 * 60 + m1))
            done = c.execute("SELECT COUNT(*) FROM tasks WHERE substr(done_at,1,10)=?",
                             (dd,)).fetchone()[0]
            out.append({"day": dd, "tracked": {k: round(v) for k, v in by.items()},
                        "tracked_total": round(sum(by.values())), "planned": planned,
                        "done": done})
    return {"week_of": days[0], "days": out}


def stop_running(c, t):
    c.execute("UPDATE sessions SET end_ts=? WHERE end_ts IS NULL", (iso(t),))


def handle_post(path, body):
    t = now()
    with _lock, db() as c:
        if path == "/api/blocks":
            b = (valid_day(body.get("day")), body.get("start", ""), body.get("end", ""))
            if not (HM_RE.match(b[1]) and HM_RE.match(b[2])) or b[2] <= b[1]:
                raise ValueError("start/end must be HH:MM and end after start")
            c.execute("INSERT INTO blocks(day,start,end,title,first_step,created_at) VALUES(?,?,?,?,?,?)",
                      b + (text(body.get("title"), "title"),
                           text(body.get("first_step"), "first_step", required=False), iso(t)))
        elif path == "/api/blocks/delete":
            c.execute("DELETE FROM blocks WHERE id=?", (int(body["id"]),))
        elif path == "/api/tasks":
            day = body.get("day")
            day = valid_day(day) if day else None
            c.execute("INSERT INTO tasks(title,day,created_at) VALUES(?,?,?)",
                      (text(body.get("title"), "title"), day, iso(t)))
        elif path == "/api/tasks/toggle":
            r = c.execute("SELECT done_at FROM tasks WHERE id=?", (int(body["id"]),)).fetchone()
            if r:
                c.execute("UPDATE tasks SET done_at=? WHERE id=?",
                          (None if r["done_at"] else iso(t), int(body["id"])))
        elif path == "/api/tasks/move":   # to a day, or backlog (day empty)
            day = body.get("day")
            c.execute("UPDATE tasks SET day=? WHERE id=?",
                      (valid_day(day) if day else None, int(body["id"])))
        elif path == "/api/tasks/delete":
            c.execute("DELETE FROM tasks WHERE id=?", (int(body["id"]),))
        elif path == "/api/start":
            stop_running(c, t)
            task_id = body.get("task_id")
            label = text(body.get("label"), "label", 120)
            c.execute("INSERT INTO sessions(label,task_id,start_ts) VALUES(?,?,?)",
                      (label, int(task_id) if task_id else None, iso(t)))
        elif path == "/api/stop":
            stop_running(c, t)
        elif path == "/api/sessions/delete":
            c.execute("DELETE FROM sessions WHERE id=?", (int(body["id"]),))
        elif path == "/api/samples":
            items = body if isinstance(body, list) else [body]
            for s in items[:500]:
                ts = s.get("ts") or iso(t)
                dt.datetime.fromisoformat(ts)
                c.execute("INSERT INTO samples(host,ts,app,title,idle_s) VALUES(?,?,?,?,?)",
                          (text(s.get("host"), "host", 80), ts[:19],
                           text(s.get("app"), "app", 120, False),
                           text(s.get("title"), "title", 300, False),
                           int(s.get("idle_s") or 0)))
        else:
            return None
    return {"ok": True}


# ---------------------------------------------------------------- HTTP

class Handler(BaseHTTPRequestHandler):
    server_version = "FocusBoard/1"

    def log_message(self, fmt, *args):
        if os.environ.get("FOCUSBOARD_LOG"):
            sys.stderr.write("%s %s\n" % (self.address_string(), fmt % args))

    def send(self, code, payload, ctype="application/json"):
        data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        try:
            day = valid_day(q.get("day") or dt.date.today().isoformat())
            if u.path in ("/", "/index.html"):
                with open(os.path.join(HERE, "index.html"), "rb") as f:
                    return self.send(200, f.read(), "text/html")
            if u.path == "/api/day":
                return self.send(200, get_day(day))
            if u.path == "/api/timeline":
                return self.send(200, timeline(day, int(q.get("bucket", 10))))
            if u.path == "/api/week":
                return self.send(200, week(day))
            if u.path == "/healthz":
                return self.send(200, {"ok": True})
        except ValueError as e:
            return self.send(400, {"error": str(e)})
        self.send(404, {"error": "not found"})

    def do_POST(self):
        # JSON content type forces a CORS preflight (which we never answer), so
        # other web pages in the browser can't post here.
        if not (self.headers.get("Content-Type") or "").startswith("application/json"):
            return self.send(415, {"error": "send application/json"})
        try:
            n = int(self.headers.get("Content-Length") or 0)
            if n > 1_000_000:
                return self.send(413, {"error": "too large"})
            body = json.loads(self.rfile.read(n) or b"{}")
            res = handle_post(urlparse(self.path).path, body)
        except (ValueError, KeyError, TypeError) as e:
            return self.send(400, {"error": str(e)})
        if res is None:
            return self.send(404, {"error": "not found"})
        self.send(200, res)


def main():
    init_db()
    host = os.environ.get("FOCUSBOARD_HOST", "127.0.0.1")
    port = int(os.environ.get("FOCUSBOARD_PORT", "5070"))
    srv = ThreadingHTTPServer((host, port), Handler)
    print("FocusBoard on http://%s:%d (db %s)" % (host, port, DB_PATH), flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

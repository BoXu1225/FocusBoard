#!/usr/bin/env python3
"""FocusBoard blocker: blocks distracting sites for every app via /etc/hosts.

Reads a blocklist (domains + optional time windows) and, when a window is
active, writes those domains into a marked section of /etc/hosts pointing at
0.0.0.0 / ::. Outside the windows the section is removed. Runs every minute
as root (launchd on macOS, cron on Linux; see install.sh).

    block.py apply   [--config PATH] [--hosts PATH]   # sync hosts with the schedule
    block.py status  [--config PATH] [--hosts PATH]   # print what's blocked and why
    block.py pause MINUTES [--config PATH]            # lift the block for a while
    block.py resume  [--config PATH]                  # end a pause early
    block.py off     [--hosts PATH]                   # remove the section now

Blocklist format (one item per line, '#' comments):

    when mon-fri 19:00-22:00     # blocking windows, local time; none = always
    when sat,sun 13:00-18:00     # end before start wraps past midnight
    x.com                        # domains; the www. variant is added for you
    twitter.com

Python 3.9+, standard library only.
"""
import argparse
import datetime as dt
import os
import re
import subprocess
import sys
import tempfile

BEGIN = "# >>> focusboard >>>"
END = "# <<< focusboard <<<"
DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
DOMAIN_RE = re.compile(r"^(?=.{1,253}$)([a-z0-9-]{1,63}\.)+[a-z]{2,63}$")
DEFAULT_CONFIG = "/usr/local/etc/focusboard/blocklist"
DEFAULT_HOSTS = "/etc/hosts"


class ConfigError(ValueError):
    pass


def parse_days(spec):
    """'mon-fri' / 'sat,sun' / 'daily' -> set of weekday ints (Mon=0)."""
    spec = spec.lower()
    if spec in ("daily", "all", "every"):
        return set(range(7))
    out = set()
    for part in spec.split(","):
        if "-" in part:
            a, b = part.split("-", 1)
            if a not in DAYS or b not in DAYS:
                raise ConfigError("bad day range: %s" % part)
            i, j = DAYS.index(a), DAYS.index(b)
            k = i
            while True:
                out.add(k)
                if k == j:
                    break
                k = (k + 1) % 7
        else:
            if part not in DAYS:
                raise ConfigError("bad day: %s" % part)
            out.add(DAYS.index(part))
    return out


def parse_hm(s):
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", s)
    if not m or int(m.group(1)) > 24 or int(m.group(2)) > 59:
        raise ConfigError("bad time: %s" % s)
    return int(m.group(1)) * 60 + int(m.group(2))


def parse_config(text):
    """Return (windows, domains). windows = [(days:set, start_min, end_min)]."""
    windows, domains = [], []
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.split("#", 1)[0].strip().lower()
        if not line:
            continue
        if line.startswith("when "):
            parts = line.split()
            if len(parts) != 3 or "-" not in parts[2]:
                raise ConfigError("line %d: expected 'when <days> HH:MM-HH:MM'" % n)
            a, b = parts[2].split("-", 1)
            windows.append((parse_days(parts[1]), parse_hm(a), parse_hm(b)))
            continue
        d = line
        for prefix in ("https://", "http://"):
            if d.startswith(prefix):
                d = d[len(prefix):]
        d = d.split("/", 1)[0]
        if not DOMAIN_RE.match(d):
            raise ConfigError("line %d: not a domain: %s" % (n, raw.strip()))
        for v in (d, d if d.startswith("www.") else "www." + d):
            if v not in domains:
                domains.append(v)
    return windows, domains


def in_window(windows, now):
    """True if `now` (naive local datetime) falls in any window; no windows = always."""
    if not windows:
        return True
    minute = now.hour * 60 + now.minute
    wd = now.weekday()
    for days, start, end in windows:
        if start <= end:
            if wd in days and start <= minute < end:
                return True
        else:  # wraps midnight: the part after midnight belongs to the previous day
            if wd in days and minute >= start:
                return True
            if (wd - 1) % 7 in days and minute < end:
                return True
    return False


def pause_file(config_path):
    return os.path.join(os.path.dirname(os.path.abspath(config_path)), "pause_until")


def paused_until(config_path, now):
    try:
        with open(pause_file(config_path)) as f:
            until = dt.datetime.fromisoformat(f.read().strip())
    except (OSError, ValueError):
        return None
    return until if until > now else None


def render_section(domains):
    lines = [BEGIN, "# managed by FocusBoard blocker - edit the blocklist, not this"]
    for d in domains:
        lines.append("0.0.0.0 %s" % d)
        lines.append(":: %s" % d)
    lines.append(END)
    return "\n".join(lines) + "\n"


def strip_section(hosts_text):
    out, skipping = [], False
    for line in hosts_text.splitlines(keepends=True):
        s = line.strip()
        if s == BEGIN:
            skipping = True
            continue
        if s == END:
            skipping = False
            continue
        if not skipping:
            out.append(line)
    text = "".join(out)
    if text and not text.endswith("\n"):
        text += "\n"
    return text


def desired_hosts(hosts_text, domains, block):
    base = strip_section(hosts_text)
    if block and domains:
        return base + render_section(domains)
    return base


def write_hosts(path, text):
    """Replace the hosts file atomically when possible, keeping its mode."""
    d = os.path.dirname(os.path.abspath(path))
    try:
        mode = os.stat(path).st_mode & 0o777
    except OSError:
        mode = 0o644
    try:
        fd, tmp = tempfile.mkstemp(prefix=".hosts.focusboard.", dir=d)
        with os.fdopen(fd, "w") as f:
            f.write(text)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except OSError:
        # e.g. a bind-mounted /etc/hosts that can't be renamed over
        try:
            os.unlink(tmp)
        except Exception:
            pass
        with open(path, "w") as f:
            f.write(text)


def flush_dns():
    cmds = []
    if sys.platform == "darwin":
        cmds = [["dscacheutil", "-flushcache"], ["killall", "-HUP", "mDNSResponder"]]
    else:
        cmds = [["resolvectl", "flush-caches"]]
    for c in cmds:
        try:
            subprocess.run(c, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
        except Exception:
            pass


def load(config_path):
    with open(config_path) as f:
        return parse_config(f.read())


def decide(config_path, now):
    """Return (block:bool, reason:str, domains)."""
    windows, domains = load(config_path)
    until = paused_until(config_path, now)
    if until:
        return False, "paused until %s" % until.strftime("%H:%M"), domains
    if in_window(windows, now):
        return True, "always on" if not windows else "inside a blocking window", domains
    return False, "outside blocking windows", domains


def local_status(config_path=DEFAULT_CONFIG, hosts_path=DEFAULT_HOSTS, now=None):
    """This machine's blocker state for the board, readable without root.

    state: "blocked" | "open" | "paused" | "not installed" | "error"
    """
    now = now or dt.datetime.now()
    try:
        with open(hosts_path) as f:
            active = BEGIN in f.read()
    except OSError as e:
        return {"state": "error", "reason": "can't read hosts: %s" % e}
    if not os.path.exists(config_path):
        if active:
            return {"state": "blocked", "reason": "hosts section present, no blocklist installed"}
        return {"state": "not installed", "reason": "run sudo ./blocker/install.sh"}
    try:
        block, reason, domains = decide(config_path, now)
    except (OSError, ConfigError) as e:
        return {"state": "error", "reason": str(e)}
    state = "blocked" if active else ("paused" if reason.startswith("paused") else "open")
    if block != active:  # the minute job hasn't caught up (or isn't running)
        reason += "; job should %s it" % ("apply" if block else "lift")
    return {"state": state, "reason": reason, "sites": len(domains)}


def cmd_apply(args):
    now = dt.datetime.now()
    try:
        block, reason, domains = decide(args.config, now)
    except (OSError, ConfigError) as e:
        print("focusboard: %s; leaving hosts unchanged" % e, file=sys.stderr)
        return 1
    with open(args.hosts) as f:
        cur = f.read()
    new = desired_hosts(cur, domains, block)
    if new != cur:
        write_hosts(args.hosts, new)
        flush_dns()
        print("%s blocking %s (%s)" % (now.strftime("%F %H:%M"), "ON" if block else "OFF", reason))
    return 0


def cmd_status(args):
    now = dt.datetime.now()
    block, reason, domains = decide(args.config, now)
    with open(args.hosts) as f:
        active = BEGIN in f.read()
    print("hosts section: %s" % ("present (sites blocked)" if active else "absent"))
    print("schedule says: %s (%s)" % ("block" if block else "allow", reason))
    print("domains: %s" % ", ".join(domains))
    return 0


def cmd_pause(args):
    until = dt.datetime.now() + dt.timedelta(minutes=args.minutes)
    with open(pause_file(args.config), "w") as f:
        f.write(until.isoformat(timespec="seconds"))
    print("paused until %s; takes effect within a minute" % until.strftime("%H:%M"))
    args.hosts = getattr(args, "hosts", DEFAULT_HOSTS)
    return cmd_apply(args)


def cmd_resume(args):
    try:
        os.unlink(pause_file(args.config))
    except FileNotFoundError:
        pass
    print("pause cleared")
    return cmd_apply(args)


def cmd_off(args):
    with open(args.hosts) as f:
        cur = f.read()
    new = strip_section(cur)
    if new != cur:
        write_hosts(args.hosts, new)
        flush_dns()
    print("section removed (it comes back on the next run unless the job is uninstalled or paused)")
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("apply", "status", "pause", "resume", "off"):
        s = sub.add_parser(name)
        s.add_argument("--config", default=DEFAULT_CONFIG)
        s.add_argument("--hosts", default=DEFAULT_HOSTS)
        if name == "pause":
            s.add_argument("minutes", type=int)
    args = p.parse_args(argv)
    return {"apply": cmd_apply, "status": cmd_status, "pause": cmd_pause,
            "resume": cmd_resume, "off": cmd_off}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())

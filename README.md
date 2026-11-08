# FocusBoard

A small, self-hosted focus tool: plan your day in a few time blocks, keep a todo
list, track what you're doing, and block the sites your hands open on reflex.

It's a lighter take on the idea behind [Super Productivity](https://super-productivity.com):
no projects, tags or integrations. Python 3.9+ standard library only, one SQLite
file, runs on macOS and Linux.

## Parts

| Part | What it does | Runs as |
|---|---|---|
| `focusboard/server.py` | Web app: **Today** (plan blocks with a first step, now-doing timer, todos, backlog), **Timeline** (planned vs timer vs foreground app), **Week** (planned vs tracked minutes, todos done) | your user |
| `blocker/block.py` | Writes the blocklist into a marked section of `/etc/hosts` during your blocking hours, so the sites fail to load in **every** app and browser | root, every minute |
| `sampler/sampler.py` | Once a minute, records the foreground app (and on Linux the window title) for the Timeline, and reports this machine's blocker state, shown per device in the header | your user, at login |

The blocker deliberately just **blocks**: no redirect. A blocked site shows a
connection error, which is enough to break the reflex. (Redirecting an HTTPS
site to your own page would need a locally trusted certificate authority; not
worth the risk.)

## Quick start

```sh
git clone https://github.com/BoXu1225/FocusBoard && cd FocusBoard

# 1. The web app (http://127.0.0.1:5070 by default; see "One board, many machines")
./run_server.sh

# 2. The blocker: edit the list, then install it (needs sudo, re-run after edits)
cp blocklist.example blocklist && $EDITOR blocklist
sudo ./blocker/install.sh
focusblock status

# 3. Optional: the activity sampler
./sampler/install.sh http://127.0.0.1:5070
```

## The blocklist

```text
when mon-fri 19:00-22:00     # blocking windows (local time); no `when` = always
when sat,sun 13:00-18:00     # end before start wraps past midnight
x.com                        # one domain per line; www. is added for you
bilibili.com
space.bilibili.com           # /etc/hosts has no wildcards: list subdomains you use
```

The installed copy lives in `/usr/local/etc/focusboard/blocklist`, owned by root.
Changing it, pausing, or switching it off all take `sudo`: that small bit of
friction is the point.

```sh
focusblock status             # what's blocked and why
sudo focusblock pause 15      # lift it for 15 minutes
sudo focusblock resume        # end the pause
sudo ./blocker/install.sh --uninstall
```

Notes:
- A tab that's already open may keep working until its connection drops; new
  page loads are blocked within a minute.
- Browsers normally check `/etc/hosts` before their own secure DNS. A VPN or app with its own DNS
  may not.
- It doesn't cover your phone: use Screen Time / Digital Wellbeing there.

## One board, many machines

Run the server on one always-on machine and open it from the others. With
[Tailscale](https://tailscale.com), `run_server.sh` binds to that machine's
Tailscale IP, so the board is reachable from your devices but not the local
network. There's no login, so don't expose it any wider.

To start it at boot on Linux, add to `crontab -e`:

```cron
@reboot /path/to/FocusBoard/run_server.sh >> /path/to/FocusBoard/server.log 2>&1
```

Install the blocker and sampler on each computer you use, pointing the sampler at
the shared server:

```sh
./sampler/install.sh http://100.x.y.z:5070
```

## Daily use

1. **Evening:** plan tomorrow: 1–3 blocks, each with a concrete first step
   ("lab 3, step 1", not "study").
2. **When a block starts:** press **Start** on the "Now / Next" card.
3. **Urge to research something?** Put it in the Backlog and get back to the block.
4. **Friday:** open **Week** and compare planned vs tracked time.

## Privacy

Everything stays in the SQLite file on your server. The sampler records app
names (and window titles on Linux; pass `--no-titles` to skip them). Nothing is
sent anywhere else.

## Tests

```sh
python3 -m unittest discover -s tests
```

## Licence

MIT

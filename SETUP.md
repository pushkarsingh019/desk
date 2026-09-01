# Setting up the desk

Instructions for an agent installing Desk on its user's machine. Read
`README.md` for what the desk *is*; this file is how to get it running and how
to prove it is.

Work these steps in order. Each ends on something you can check.

## 1. Establish the ground

The desk server must run on **this** machine — the one you are executing on —
because watching a file is a filesystem operation. If you are on a remote
sandbox, a container, or a box the user does not sit in front of, say so and
stop. Installing there produces a desk the user cannot see.

Then check what is here:

```
uname -s                       # Darwin, Linux, or something else
command -v uv || ls ~/.local/bin/uv
pwd                            # where the repo is checked out
```

`uv` is the one prerequisite. If it is missing, install it:

```
curl -LsSf https://astral.sh/uv/install.sh | sh
```

**On macOS, the repo must not be under `~/Documents`, `~/Desktop`, or
`~/Downloads`.** launchd cannot read from those, and the failure is silent —
the desk binds its port, never listens, and logs nothing. `install.sh` refuses
to run from there. Move the repo first:

```
mv <repo> ~/code/desk
```

Done when you know the platform, `uv` runs, and the repo is somewhere launchd
can reach.

## 2. Decide the address

The desk has no TLS and no login. The address it binds is the entire
perimeter, so this decision is the user's, not yours. Ask, unless they have
already said:

> Do you want the desk on this machine only, or reachable from your other
> machines?

- **This machine only** → nothing to do. The installer binds `127.0.0.1`.
- **Other machines too** → they need [Tailscale](https://tailscale.com)
  installed and up on both. Check with `tailscale status`. The installer
  detects a live tailnet and binds there by itself.

If they name a specific address instead — a LAN IP, a VPN interface — pass it
as `DESK_HOST` and tell them plainly that anyone who can reach that address has
their desk.

Done when you can say which of the two the install will pick, and why.

## 3. Install

```
sh scripts/install.sh
```

It prints what it decided, line by line: project, platform, uv, port, data
directory, log directory, bind mode, URL, the `desk` command, and every skill
directory it linked. Read those lines — they are the record of what the install
actually did, and they are the first thing to quote if a later step fails.

To override anything, set it in the environment for that one run:

```
DESK_PORT=7788 DESK_BIND=local sh scripts/install.sh
```

Done when the script exits zero and its last lines are a `desk status` report
and `desk: installed.`

## 4. Prove it works

Three checks, in this order. Each one rules out a different failure.

```
desk status
```

Prints the URL, the sheet counts, the data directory, and the log path. If the
shell cannot find `desk`, call `~/.local/bin/desk` and tell the user their PATH
is missing `~/.local/bin`.

```
curl -sS -o /dev/null -w '%{http_code}\n' "$(desk status | head -1)"
```

Expect `200`. This is the difference between a process that started and a
server that answers.

Then present a real figure, because that is the path the user will actually
take:

```
mkdir -p /tmp/desk-check
printf '%s' '<svg xmlns="http://www.w3.org/2000/svg" width="240" height="120"><rect width="240" height="120" fill="#c8e6c9"/><text x="20" y="66" font-family="sans-serif" font-size="20">desk works</text></svg>' > /tmp/desk-check/hello.svg
desk present /tmp/desk-check/hello.svg
```

Expect `hello.svg v1 — waiting in the inbox` and the URL.

Done when all three pass. Tell the user to open the URL, and that a test sheet
is waiting in the inbox down the left edge — they can drag it out, or throw it
away with the `×`.

## 5. Hand over

Tell them, in this order:

1. The URL, and that it is a page to leave open.
2. How to present: `/desk` in Claude Code or Pi, `$desk` in Codex or T3 Code —
   with no argument for the newest figure, or with a path for a specific one.
3. That presenting once is enough. The desk watches the file; re-running the
   script updates the sheet in place.
4. Which agents got the skill — quote the `desk: skill ->` lines. If none were
   linked, their agent keeps skills somewhere the installer does not know, and
   they can name it:
   `DESK_SKILL_DIRS="$HOME/.myagent/skills/desk" sh scripts/install.sh`

## When something fails

| symptom | what it is | fix |
|---|---|---|
| `install: no uv found` | no uv, or it is somewhere unusual | install uv, or set `DESK_UV=/path/to/uv` |
| install refuses: TCC-protected directory | the repo is under `~/Documents`, `~/Desktop`, or `~/Downloads` | move the repo and re-run |
| `the server did not come up` | it started and died | read the log path the installer printed; it holds the traceback |
| `no tailscale address found` | bound to `tailnet`, but Tailscale is down | bring Tailscale up, or reinstall with `DESK_BIND=local` |
| `Address already in use` in the log | something else holds the port | `DESK_PORT=7788 sh scripts/install.sh` |
| `desk: command not found` | `~/.local/bin` is not on PATH | call `~/.local/bin/desk`, and tell the user to add it |
| `could not reach the desk` | the server is not answering | `desk status`, then the log |
| `is not a desk file type` | the desk takes `.svg`, `.png`, `.pdf`, `.html`, `.md` | render it to a self-contained HTML file and present that |
| the desk is up but a sheet never updates | the file changed on a *different* machine from the server | present from the machine running the desk |

Read the log before guessing. The desk writes a traceback for anything that
kills it, and `desk status` prints the log's path on every platform.

To check the service itself:

```
launchctl print "gui/$(id -u)/dev.desk"     # macOS
systemctl --user status desk.service        # Linux
```

## Settings

All optional, all read from the environment. Set them for the `install.sh` run
and they are baked into the service it writes, so once is enough.

| variable | default |
|---|---|
| `DESK_PORT` | `7777` |
| `DESK_DATA_DIR` | `~/.desk` |
| `DESK_BIND` | `tailnet` if Tailscale is up, else `local` |
| `DESK_HOST` | unset — an explicit bind address, overriding `DESK_BIND` |
| `DESK_HOSTNAME` | the name shown in the URL, if it differs from the address |
| `DESK_LOG_DIR` | `~/Library/Logs/desk` on macOS, `~/.local/state/desk` elsewhere |
| `DESK_DEBOUNCE` | `0.3` seconds |
| `DESK_POLL_INTERVAL` | `0.1` seconds |
| `DESK_UV` | wherever `uv` is found |
| `DESK_BIN_DIR` | `~/.local/bin` |
| `DESK_SKILL_DIRS` | the known agent skill directories |

## What the install wrote

| platform | service | survives logout |
|---|---|---|
| macOS | `~/Library/LaunchAgents/dev.desk.plist` | yes |
| Linux | `~/.config/systemd/user/desk.service` | after `loginctl enable-linger` |
| anything else | nothing — the user starts the server themselves | up to them |

Everywhere: the `desk` command at `~/.local/bin/desk`, a symlink to
`skill/desk` in each agent's skills directory, and the sheets in `~/.desk`.

To run the server in the foreground instead — for a platform with no service,
or to watch it start:

```
.venv/bin/python -m desk.server
DESK_BIND=local DESK_PORT=7788 .venv/bin/python -m desk.server
```

It binds one address and nothing else, never `0.0.0.0`.

## Removing it

```
sh scripts/uninstall.sh
```

Removes the service, the `desk` command, and the skill links. The sheets stay
in `~/.desk` — say so, and delete that directory only if the user asks.

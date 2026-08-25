# Desk

A visuo-spatial sketchpad for agent-produced figures.

You run a coding agent over SSH on `pushkar-studio`. It makes a plot. You type
`/desk`. The figure appears on a web page you keep open on another machine,
where you put it, at the size you made it. When the agent re-runs the script,
the sheet updates in place — same position, same size — so you can keep staring
at one spot while it iterates.

Served over Tailscale at **`http://pushkar-studio.taila96c04.ts.net:7777`**.

Port `7777` was checked against the launchd agents already running on studio
(grafana 4000, prometheus 9090, mongodb 27017, ollama 11434, the dad-hermes
dashboard 9180, and the docker-mapped 80/3000/3010/8081/8090) and does not
collide with any of them.

## Using it

Type `/desk` in Claude Code:

```
/desk                      # the newest figure you just made
/desk path/to/figure.svg   # that one specifically
```

The figure lands in the **inbox** — the strip down the left edge. Nothing is
ever placed on the desk for you; drag it out and put it where you want it.

From then on the desk **watches** that file. Re-run the script and the sheet
updates itself: the image swaps, a highlight ring flashes for about half a
second, and nothing moves.

| gesture | what happens |
|---|---|
| drag a sheet's title bar | move it |
| drag its bottom-right corner | resize it |
| drag one sheet onto another | make a pile |
| click a pile | fan it open; click again to collapse |
| drag a sheet out of a fanned pile | take it back out |
| double-click a sheet | fullscreen, with its own pan and zoom |
| `Escape` | leave fullscreen |
| `×` on a sheet, or drag it to the trash zone | throw it away |
| **trash** in the corner | see what you threw away, and restore it |
| drag the desk background | pan |
| trackpad pinch, or ⌘-scroll | zoom |
| `0` | home |
| `f` | overview — fit everything on screen |

Image sheets drag from anywhere on them. Markdown, HTML, and PDF sheets drag by
their title bar, so the plot inside stays interactive.

Throwing a sheet away leaves a **tombstone**: the desk stops watching that path,
and a later change to the file will *not* bring the sheet back. An explicit
`/desk` on that path clears the tombstone and returns it to the inbox.

Figures may be `.svg`, `.png`, `.pdf`, `.html`, or `.md`. Anything else —
render it to a self-contained HTML file and present that.

Two names never reach the desk, and they have deliberately different reach.
`*_tmp*` is checked against the whole path, because a half-written `savefig`
usually lands in a scratch *directory* and keeping that off the desk is the
point of the rule. A dotfile is checked against the file itself only: plenty of
good figures live under a hidden directory somebody else chose — `~/.claude/`,
`~/.cache/`, a tool's state directory — and you did not hide those.

## Installing

```
sh scripts/install.sh
```

One command. It builds the pinned environment with `uv`, installs the `desk`
command to `~/.local/bin/desk`, copies the `/desk` skill into
`~/.claude/skills/desk/`, writes a launchd user agent, starts it, and prints
the desk URL. `sh scripts/uninstall.sh` reverses all of that and leaves your
sheets alone.

**The project must not live in `~/Documents`, `~/Desktop`, or `~/Downloads`.**
macOS protects those behind TCC, and a process launchd starts has no way to
answer the permission prompt — the desk would bind its port, never listen, and
log nothing at all. `install.sh` refuses to install from those directories and
tells you what to do. `~/code/desk` is a good home.

Settings, all optional, read from the environment:

| variable | default |
|---|---|
| `DESK_PORT` | `7777` |
| `DESK_DATA_DIR` | `~/.desk` |
| `DESK_HOST` | the Tailscale address; set this to bind elsewhere |
| `DESK_DEBOUNCE` | `0.3` seconds |
| `DESK_POLL_INTERVAL` | `0.1` seconds |

Logs are at `~/Library/Logs/desk/desk.log`. `desk status` says where the desk is
and what is on it.

## Running it by hand

```
.venv/bin/python -m desk.server          # foreground, binds to the tailnet
DESK_HOST=127.0.0.1 DESK_PORT=7788 .venv/bin/python -m desk.server
```

The server binds to the Tailscale interface and nothing else — not the LAN, not
even `127.0.0.1`. There is no TLS and no login, because Tailscale is the
authentication boundary. That is deliberate.

## How it is built

Python, pinned and run through `uv` so the server never touches the system
Python on a machine used for science. The frontend is vanilla JS and CSS with no
build step, served static. No runtime dependency but `markdown`.

| module | owns |
|---|---|
| `store.py` | the desk's own copy of every file, sheet records, versions, tombstones |
| `layout.py` | desk state — position, size, z-order, piles, inbox, viewport — as pure transitions |
| `watcher.py` | polling watched source paths, debounced so a half-written `savefig` is never ingested |
| `server.py` | the HTTP API: publish, state, content, layout, the SSE stream, static assets |
| `render.py` | markdown to a self-contained page, and the visible failure when a file is unreadable |
| `cli.py` | the `desk` command the `/desk` skill runs |
| `web/` | the page |

**Sheet identity is the absolute source path.** That one rule is what makes
update-in-place the default with no cooperation from the agent, and it is why
there is no watch registry, no drop directory, no flood cap, and no inbox
overflow logic. Watching is implicit: publishing a path subscribes to it, and
nothing that has never been published can reach the desk.

The launchd agent runs `.venv/bin/python` directly rather than `uv run`. At boot
the desk must not depend on the network, the uv cache, or a lock another uv
process is holding; `uv sync` at install time is what puts that interpreter
there.

## Tests

```
~/.local/bin/uv run pytest
```

Two seams, both as high as possible.

**The HTTP API** (`tests/test_api.py`) drives a real server in a real
subprocess, over HTTP, and nothing else. Store, watcher, debounce, versioning,
tombstones, restart survival, and the `desk` command are all covered here, with
no unit test beneath any of it.

**The layout model** (`tests/test_layout.py`) tests desk state transitions
directly as pure functions over a plain object. No DOM, no server, no I/O.

Pixel rendering, drag physics, pan/zoom feel, and launchd integration are
verified by looking at the thing. That is a deliberate trade to avoid a
browser-automation dependency.

## The design

- `SPEC.md` — the full specification: problem, solution, 40 user stories,
  implementation and testing decisions, and what was deliberately left out.
  Read its "Out of Scope" section before adding anything.
- `CONTEXT.md` — the domain glossary. Use this vocabulary in code and commits.
- `CLAUDE.md` — instructions for agents working on this.
- `.scratch/desk/issues/` — the thirteen tickets, in dependency order.

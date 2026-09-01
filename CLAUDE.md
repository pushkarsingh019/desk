# Instructions for agents working on Desk

Read `CONTEXT.md` first and use its vocabulary exactly. Read `SPEC.md` for the
full design and — importantly — its "Out of Scope" section, which records what
was deliberately cut and why. Do not reintroduce cut features.

## Issue tracker

Local files. One ticket per file under `.scratch/desk/issues/`, numbered in
dependency order. Each declares its blockers. Work the **frontier**: any ticket
whose blockers are all complete. Ticket 01 is the only one that can start
immediately.

`.scratch/` is committed in this repo on purpose — the tickets travel with the
code. Do not add it to `.gitignore`.

Mark acceptance criteria as you complete them. Do not modify a ticket's
blocking edges.

## Where this runs

Anyone's machine. The maintainer's is `pushkar-studio` — macOS 27, arm64,
system `python3` 3.9.6, no node — and that is a test case, not an assumption.

**Keep the platform in `scripts/`.** OS-specific code lives in the install
scripts and in three named helpers: `default_log_dir` in `cli.py`, and
`tailscale_address` / `_interface_addresses` in `server.py`. Everything else is
plain Python that runs anywhere Python does, and stays that way. `SETUP.md` is
the install written for someone else's agent — change it whenever you change
`install.sh`.

The server must run on the same machine as the agent, because watching is a
filesystem operation. The user may browse the desk from anywhere.

## Stack

- Python, pinned and run via `uv`, so the server never touches the system Python
  on a machine used for science. `uv` is usually at `~/.local/bin/uv`, which is
  **not** on the PATH of non-interactive shells or a service manager — use
  absolute paths anywhere the environment is not a login shell.
- Frontend is vanilla JS and CSS with **no build step**. Served static.
- A launchd user agent (macOS) or systemd user unit (Linux) keeps the server
  alive across reboots.
- Plain HTTP bound to exactly one address — the tailnet if there is one, else
  `127.0.0.1`. No TLS, no tokens: the bind address is the whole perimeter. That
  is a deliberate decision, not an oversight.

## Testing

```
uv run pytest
```

Two seams, both as high as possible. Test external behaviour only; never reach
into internal state.

**Seam 1 — the HTTP API.** Drive the entire server through it. Store, watcher,
tombstones, debounce, and versioning are all covered here, with no unit tests
beneath.

**Seam 2 — the layout model.** Desk state transitions (place, move, resize,
pile, unpile, trash, restore, inbox membership, z-order) are pure functions over
a plain state object, tested directly.

The DOM layer stays thin enough to need no tests. Pixel rendering, drag physics,
pan/zoom feel, and service integration are verified by looking at the thing —
this is a deliberate trade to avoid a browser-automation dependency.

If you find yourself wanting a third seam, that is a signal the module shape is
wrong. Fix the shape.

## Conventions

- Every ticket is a tracer bullet: a narrow but complete path through store,
  API, page, and tests. Never land a horizontal slice of one layer.
- Prefer deleting a feature over defending against it. Most of this design's
  quality came from cuts.

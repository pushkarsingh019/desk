# Desk

A visuo-spatial sketchpad for agent-produced figures.

Your coding agent makes a plot. You type `/desk`. The figure appears on a web
page you keep open — where you put it, at the size you made it. When the agent
re-runs the script, the sheet updates in place, same position, same size, so
you can keep staring at one spot while it iterates.

It runs on the machine your agent runs on, macOS or Linux. You browse it there,
or from your laptop across the room over Tailscale.

## Install

```
git clone https://github.com/pushkarsingh019/desk.git ~/code/desk
cd ~/code/desk
sh scripts/install.sh
```

You need [uv](https://docs.astral.sh/uv/) and nothing else. The installer builds
the environment, installs the `desk` command, links the skill into your agents,
starts a server that survives reboots, and prints the URL. `sh
scripts/uninstall.sh` undoes all of it and leaves your sheets alone.

On macOS, keep the repo out of `~/Documents`, `~/Desktop`, and `~/Downloads` —
launchd cannot read those, and the installer will say so.

## Using it

Type `/desk` in Claude Code or Pi, `$desk` in Codex or T3 Code:

```
/desk                      # the newest figure you just made
/desk path/to/figure.svg   # that one specifically
```

Figures may be `.svg`, `.png`, `.pdf`, `.html`, or `.md`.

New sheets land in the **inbox**, the strip down the left edge. Drag one out and
put it where you want it — nothing is ever placed for you. From then on the desk
**watches** that file: present it once, and re-running the script updates the
sheet by itself.

| gesture | what happens |
|---|---|
| drag a sheet's title bar | move it |
| drag its bottom-right corner | resize it |
| drag one sheet onto another | make a pile |
| click a pile | fan it open; click again to collapse |
| double-click a sheet | fullscreen, with its own pan and zoom |
| `×` on a sheet, or drag it to the trash zone | throw it away |
| **trash** in the corner | see what you threw away, and restore it |
| drag the desk background | pan |
| trackpad pinch, or ⌘-scroll | zoom |
| `0` / `f` | home / fit everything on screen |

## Agents

[`SETUP.md`](SETUP.md) is this install written for an agent to carry out, with
the checks and the failure modes spelled out. Point yours at it:

> read SETUP.md and set up the desk

Settings, the choice of what address the desk binds, and troubleshooting all
live there.

## Design

- [`SPEC.md`](SPEC.md) — the full specification, and what was deliberately left
  out. Read its "Out of Scope" section before adding anything.
- [`CONTEXT.md`](CONTEXT.md) — the domain glossary.
- [`CLAUDE.md`](CLAUDE.md) — instructions for agents working on the code.

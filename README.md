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
/desk                                  # the newest figure you just made
/desk path/to/figure.svg               # that one specifically
/desk path/to/figure.svg --to bobs-mac # on Bob's desk instead of yours
/desk feedback                         # the agent reads the comments you pinned on what it just put up
/desk feedback path/to/figure.svg      # on that sheet only
/desk feedback --all                   # on every sheet on the desk
```

Figures may be `.svg`, `.png`, `.pdf`, `.html`, or `.md`.

New sheets land in the **inbox**, the strip down the left edge. Drag one out and
put it where you want it — nothing is ever placed for you. From then on the desk
**watches** that file: present it once, and re-running the script updates the
sheet by itself.

**Feedback.** When something is wrong with a figure, do not describe it —
point at it. Double-click the sheet, press `c`, and drag a rectangle (or
click a point) on the spot; type a few words; `Enter`. Then type `/desk
feedback` and the agent reads your comments back with where each one is, in
pixels and in words, fixes the plotting code, and re-runs it so the sheet
updates under your pins. Look, and **resolve** what is fixed — the agent never
does; it cannot write, resolve, or remove a comment. A `/desk` on a sheet with
open comments tells the agent to read them first. A desk holds many figures,
so bare `/desk feedback` covers the ones made from the directory the agent is
working in, newest first; `--all` is the whole desk. The same thing from a
shell is `desk feedback [path] [--all] [--json]`.

**Someone else's desk.** `--to` names a machine on your tailnet that runs a
desk. The figure lands in *their* inbox, marked with where it came from, and
where it goes from there is up to them. It is a copy: their desk cannot watch
your file, so send it again to update it — same sheet, same spot. Their desk
has to be bound to the tailnet, which the installer does whenever Tailscale is
up.

| gesture | what happens |
|---|---|
| drag a sheet's title bar | move it |
| drag its bottom-right corner | resize it |
| drag one sheet onto another | make a pile |
| click a pile | fan it open; click again to collapse |
| double-click a sheet | fullscreen, with its own pan and zoom |
| in fullscreen, **comment** or `c`, then drag a rectangle or click a point | pin a comment there; type, `Enter` saves, `Esc` cancels |
| in fullscreen, click a pin | read the comment; **resolve** it when it is dealt with, or **remove** it |
| `×` on a sheet, or drag it to the trash zone | throw it away |
| click a sheet, then `Delete` (or `Backspace`) | throw it away |
| **trash** in the corner | see what you threw away, and restore it |
| **clear** in the corner | throw every sheet away at once — each one can still be restored |
| drag the desk background | pan |
| trackpad pinch, or ⌘-scroll | zoom |
| `0` / `f` | home / fit everything on screen |
| the desk switcher in the corner | bring another desk out; `+ desk` starts one, `remove` takes an empty one away |

Everything lands on whichever desk is out — `/desk`, a figure someone sent
you, a sheet you restore. The others keep their layouts and keep watching
their files; switch back and they are as you left them.

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

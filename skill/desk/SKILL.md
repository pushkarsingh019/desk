---
name: desk
description: Put a figure on the desk — the page where you look at figures — or read the comments the user pinned on one.
disable-model-invocation: true
argument-hint: "[feedback] [path/to/figure.svg]"
allowed-tools: Bash(desk:*)
---

# Desk

Two things happen here, and the first word decides which:

- `/desk [path]` — **present** a figure. Run `desk present` and report the URL.
- `/desk feedback [path]` — **read the feedback** the user pinned on the desk.
  Run `desk feedback` and act on what it prints.

The first argument being exactly the word `feedback` is what tells them
apart; anything else is a path. If the shell cannot find `desk`, use
`"$HOME/.local/bin/desk"`.

## The loop

The desk is where the user looks at figures. When something is wrong with
one, they do not describe it to you; they pin a **comment** on it, in the
browser, on the exact spot. Then they come back here and type `/desk` or
`/desk feedback`. You read the comments, fix the plotting code, re-run it, and
the sheet updates in place under their eyes. They look, and **resolve** what is
fixed. That is the whole loop, and it is theirs to drive:

1. The user looks at the figure on the desk.
2. The user pins comments in the browser — a rectangle, a point, or the whole
   sheet — each with a few words.
3. The user types `/desk feedback`, or presents again and the command says
   there are open comments.
4. You read the feedback and fix the code. The watched sheet updates itself.
5. The user looks again and resolves what is fixed. What is not stays open.

You never close that loop yourself. **You never resolve, remove, edit, or
write a comment.** You cannot — there is no command for it, and that is
deliberate: a comment is the user's judgement of the figure, and only the
user says when it has been met.

## Presenting

```
desk present <the path the user gave, or nothing at all>
```

With no path the command picks the most recently modified figure under the
current directory, which is the common case. It starts the server if the port
is closed, so a cold machine works first time, and it prints the sheet's name,
its version, and the desk URL.

Done when the command has exited zero and you have repeated its URL. On a
nonzero exit, say the present failed and quote the error verbatim: a figure the
user believes is on their desk but is not is the worst outcome this tool has.

**If the output has a third line** — `2 open comments on this sheet — run:
desk feedback <path>` — the sheet you just presented has feedback waiting.
Run exactly that command and act on it (below) **before doing anything else**.
The user put those comments there for you; presenting over them without
reading them is ignoring the user.

### Presenting once is enough

The desk **watches** every path it has been given. Re-running the plotting
script updates that sheet in place by itself, so present a figure the first
time and afterwards let the file speak for itself. This is also how a fix
reaches the user: re-run the script, and the sheet under their comments
changes.

A new sheet lands in the **inbox** and waits there. Where it goes on the desk,
how big it is, and what it sits next to are the user's to decide.

The user may keep several desks and switches between them on the page. A
present lands on whichever desk is out; `desk status` says which. There is no
way to aim at another desk from here, and that is deliberate — the user
switches, then presents.

## Reading feedback

```
desk feedback             # every sheet on the current desk with open comments
desk feedback <path>      # that one sheet
desk feedback --json      # the same, as one object, if you would rather parse it
```

It never starts the server. It prints, per sheet, the source path, where it
came from if it was sent from another machine, and the current version; and
per open comment its number, the version it was made on, where it is, and the
text. "Where" comes three ways, and you should use all of them:

- a coarse place — `upper right`, `left edge, middle`, `whole sheet`;
- fractions of the figure — `x 0.750–0.950, y 0.100–0.200`;
- pixels against the current version — `pixels on v3: x 480–608, y 48–96
  (of 640×480)`. For an SVG these are the file's own viewBox units, the ones
  you will find in the file. If the comment was made on an older version
  that is still kept, pixels against that version follow too; if it says
  `no longer retained`, the picture it was made on is gone, and the fractions
  and the current pixels are what you have.

`no open feedback` means there is nothing to do; say so and stop.

### What acting on it means

For each open comment, in order:

1. **Restate it as one concrete change to the plotting code.** "The legend
   covers the data, upper right" becomes "move the legend outside the axes" or
   "put it lower left where there is no data". If a comment does not reduce
   to one change, say what you think it means and ask, rather than guessing
   at the figure.
2. **Make the change** in the script that produces the file.
3. **Re-run the script** so the watched sheet updates in place. Do not
   present it again unless the file moved; the desk already watches it.
4. **Tell the user to look at the desk and resolve what is fixed.** Say which
   comment numbers you addressed and how. Do not tell them it is fixed as if
   that were settled — they decide that, on the figure, by resolving.

A comment you could not act on — because it asks for data you do not have, or
you do not understand it — you report as such, by number, and leave open.

### A sheet with an origin

If a sheet in the report says `from <machine> — a copy`, it was sent here from
another machine. The script that made it is not on this machine, and
re-running anything here will not change it. Its feedback is to be
**reported, not acted on**: tell the user what the comments say and that the
figure's source is on `<machine>`, and stop. Do not guess at a script, and do
not present a lookalike over it.

## Someone else's desk

When the user says to put a figure on another person's desk — "put this on
Bob's desk", "send this to Bob", "show Bob" — send it there:

```
desk present <path> --to <their machine>
```

`--to` takes the name of their machine on the tailnet (`bobs-mac`), with
`:port` if their desk is not on 7777, or a full `http://` URL. The user has to
tell you the machine name; never guess one. The command prints where the sheet
landed — `waiting in bobs-mac's inbox` — and their desk's URL. Nothing is
started or published on this machine.

A sent sheet is a **copy**. Their desk cannot watch a file on this machine, so
re-running the script does not update it. Send it again and it updates in
place on their desk, same sheet, same spot. On their desk it is marked
`from <this machine>`. Comments Bob pins on it stay on Bob's desk: a send
never reports them, and `desk feedback` here never sees them.

## What the errors mean

- `is not a desk file type` — the desk takes `.svg`, `.png`, `.pdf`, `.html`
  and `.md`. Render anything else to a self-contained HTML file and present
  that instead.
- `found no figure to present` — nothing recent is lying around. Ask for a path.
- `no such file` — the path is wrong. Say so and let the user correct it.
- `is not a sheet on the desk` — `desk feedback` was given a path that is not
  on the desk that is out. Check `desk status` for which desk that is, and the
  spelling of the path.
- `not running` from `desk feedback` — the desk is down, and feedback is not
  worth starting it for. `desk status` prints the log path; a `desk present`
  would start it.
- `could not reach the desk` — quote it. `desk status` prints the log path.
- `could not reach bobs-mac's desk` — their desk is not running, or was
  installed to serve their machine only. Say so; they can check `desk status`
  on their side, and reinstall with `DESK_BIND=tailnet` to accept sends.

## The desk is on the machine you are running on

Watching is a filesystem operation, so the desk can only see files on its own
machine. If you are working on a different machine from the one the desk was
installed on — over SSH, in a container, on a remote sandbox — a figure you
present there is not on the user's desk. Say where you are rather than letting
a stale figure look fresh. If the user names the machine their desk runs on,
`--to <that machine>` still puts a copy there — say that it is a copy, and
that it will not follow the file.

---
name: desk
description: Put a figure on the user's desk — a web page on their tailnet where they look at agent-produced figures. Presents the newest figure, or one named by path.
disable-model-invocation: true
argument-hint: "[path/to/figure.svg]"
allowed-tools: Bash(desk:*)
---

# /desk

Put a figure on the user's desk so they can look at it.

Run this, and nothing else:

```
desk present $ARGUMENTS
```

If the shell cannot find `desk`, use `"$HOME/.local/bin/desk" present` instead.

Then report the URL it prints. That is the whole job.

## What the command does

- With a path, it presents that file.
- With no arguments, it presents the most recently modified figure under the
  current directory — `.svg`, `.png`, `.pdf`, `.html`, or `.md`, ignoring
  dotfiles and `*_tmp*`.
- It starts the desk server if the port is closed, so a cold machine works on
  the first try.
- It prints the sheet name, its version, and the desk URL.
- It exits nonzero and says why if anything fails.

## Rules

- **Never say a figure is on the desk unless the command exited zero.** If it
  failed, say so and repeat its error verbatim. A figure the user believes is on
  their desk but is not is the worst outcome this tool has.
- **Do not pass a path the user did not ask for.** With no argument, let the
  command choose; that is what it is for.
- **Do not try to position, size, or group anything.** Layout is the user's
  alone. A new sheet lands in the inbox and waits there until they drag it out.
- Once a file has been presented once, the desk watches it. Re-running the
  script that produced it updates the sheet by itself — do not present it again
  just because it changed.
- Do not report on whether the user looked. The desk tells you nothing about
  that, and guessing is worse than silence.

## When it fails

- `no such file` — the path is wrong. Say so; do not guess another file.
- `is not a desk file type` — the desk takes `.svg`, `.png`, `.pdf`, `.html`,
  `.md`. If the figure is in another format, render it to a self-contained HTML
  file and present that instead.
- `found no figure to present` — nothing recent is lying around. Ask the user
  for a path rather than hunting.
- `could not reach the desk` — report it verbatim. The log is at
  `~/Library/Logs/desk/desk.log`.
- The desk server must run on the same machine as you. If you are working on a
  box that is not `pushkar-studio`, say so: a sheet published from elsewhere
  would go up once and then silently stop updating.

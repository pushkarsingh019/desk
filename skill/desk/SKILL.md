---
name: desk
description: Put a figure on the desk — the page on your tailnet where you look at figures.
disable-model-invocation: true
argument-hint: "[path/to/figure.svg]"
allowed-tools: Bash(desk:*)
---

# /desk

Present a figure, then report the URL the command prints.

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

If the shell cannot find `desk`, use `"$HOME/.local/bin/desk" present`.

## Presenting once is enough

The desk **watches** every path it has been given. Re-running the plotting
script updates that sheet in place by itself, so present a figure the first
time and afterwards let the file speak for itself.

A new sheet lands in the **inbox** and waits there. Where it goes on the desk,
how big it is, and what it sits next to are the user's to decide.

## What the errors mean

- `is not a desk file type` — the desk takes `.svg`, `.png`, `.pdf`, `.html`
  and `.md`. Render anything else to a self-contained HTML file and present
  that instead.
- `found no figure to present` — nothing recent is lying around. Ask for a path.
- `no such file` — the path is wrong. Say so and let the user correct it.
- `could not reach the desk` — quote it. The log is `~/Library/Logs/desk/desk.log`.

The desk server runs on the machine you are running on. Working anywhere other
than `pushkar-studio` means a sheet publishes once and then silently stops
updating, so say where you are rather than letting a stale figure look fresh.

# Spec: Desk — a visuo-spatial sketchpad for agent-produced figures

## Problem Statement

I run coding agents over SSH on scientific work. The code they write produces
figures — usually SVG, sometimes PNG or a self-contained HTML plot. Right now
there is no good way for me to actually *look* at them.

The options all fail:

- The figure is written to a file on the remote machine. My eyes are on a
  different machine. `scp`-ing each one by hand is friction I pay on every
  iteration.
- The agent describes the plot in text. This is useless — the entire point of a
  plot is that it is not text.
- Artifacts or the Claude web app would show it side by side, but I do not want
  to work there; I work in a terminal over SSH.
- Opening it in Preview only works when the agent is on the machine my eyes are
  on, which is exactly the case I do not have.

There is a second failure that only shows up during iteration. When the agent is
refining a figure — fix the axis, rescale, re-fit — I want to keep staring at
one spot on screen while the picture underneath me updates. Every existing
option makes me re-find the figure after every single run.

And a third: figures accumulate. A session produces a dozen, of which three
matter and I want them in view together, arranged the way *I* arranged them, not
in a scrolling feed sorted by mtime.

## Solution

A single web page — my desk — served from `pushkar-studio` and reachable over my
tailnet.

I type `/desk` in Claude Code. The figure the agent just made appears on the
desk. I look at it in my browser.

The desk behaves like a physical desk. Sheets sit where I put them. I drag them
around, resize them, pile them at the edge, throw them away. Nothing moves
unless I move it.

When the agent re-runs the script that made a figure, that sheet updates **in
place** — same position, same size, no scroll jump, no reflow — with a brief
highlight ring so I know it happened. I can keep my eyes fixed on it while the
agent iterates.

## User Stories

1. As a scientist working over SSH, I want to type `/desk` and have the figure the agent just produced appear on a web page, so that I can look at it without leaving my terminal workflow.
2. As a scientist, I want `/desk` with no arguments to present the most recent figure produced in this session, so that I don't have to type or remember a path in the common case.
3. As a scientist, I want to optionally pass a path (`/desk path/to/fig.svg`), so that I can disambiguate when the session produced several figures.
4. As a scientist, I want SVG figures to render crisply at any zoom level, so that I can inspect fine detail in vector plots.
5. As a scientist, I want PNG figures to render, so that raster output from libraries that can't emit vector works too.
6. As a scientist, I want self-contained HTML plots (plotly, bokeh, altair) to render and stay interactive, so that anything I can't view natively still has a path onto the desk.
7. As a scientist, I want markdown files to render as sheets, so that a written summary can sit next to the figure it describes.
8. As a scientist, I want a single desk shared across all my work, so that there is one URL and one place to look.
9. As a scientist, I want the desk to be an infinite pannable, zoomable canvas, so that I never run out of room.
10. As a scientist, I want a key that snaps the view back home, so that I can always recover my bearings after panning away.
11. As a scientist, I want a zoomed-out overview, so that I can find something I placed a while ago.
12. As a scientist, I want to drag a sheet to any position on the desk, so that the layout reflects how I think about the figures.
13. As a scientist, I want to resize a sheet by dragging its corner, so that important figures can be large and reference figures small.
14. As a scientist, I want sheet positions and sizes to persist to disk, so that my desk survives a server restart or a machine reboot.
15. As a scientist, I want to double-click a sheet to blow it up fullscreen with pan and zoom, so that I can actually read a dense figure.
16. As a scientist, I want to dismiss the fullscreen view and find the desk exactly as I left it, so that enlarging is non-destructive to my layout.
17. As a scientist, I want newly presented sheets to land in an inbox strip at the edge of the desk, so that they never cover or displace something I positioned by hand.
18. As a scientist, I want to drag a sheet out of the inbox onto the desk, so that placement is always my decision.
19. As a scientist, I want to drag one sheet onto another to form a pile, so that shoving a group aside is one gesture instead of many.
20. As a scientist, I want to click a pile to fan it open, so that I can see what's in it without disassembling it.
21. As a scientist, I want to pull a single sheet back out of a pile, so that piles are not a trap.
22. As a scientist, I want to throw a sheet away, so that the desk doesn't accumulate junk forever.
23. As a scientist, I want a thrown-away sheet to NOT come back when the script that made it re-runs, so that discarding actually means something.
24. As a scientist, I want a trash corner I can restore from, so that discarding is recoverable when I change my mind.
25. As a scientist, I want re-presenting the same file path to update the existing sheet rather than create a second one, so that the desk doesn't fill with near-identical copies.
26. As a scientist, I want a sheet to update automatically when its source file changes on disk, so that re-running a plotting script requires no second command.
27. As a scientist, I want an updating sheet to keep its exact position and size, so that I can stare at one spot while the agent iterates.
28. As a scientist, I want a brief highlight ring when a sheet updates, so that I notice the change without being interrupted by it.
29. As a scientist, I want updates to never steal focus, scroll the page, or reflow the layout, so that my attention stays where I put it.
30. As a scientist, I want the desk to copy each figure into its own store, so that a later `rm -rf` of my output directory doesn't gut my desk.
31. As a scientist, I want previous versions of each sheet retained, so that "what did this look like before that change" is recoverable.
32. As a scientist, I want the desk to be reachable at a stable URL on my tailnet, so that I can open it from my MacBook while the agent works on studio.
33. As a scientist, I want no login, so that opening my desk is friction-free on a network that is already private.
34. As a scientist, I want the desk server to start automatically on boot, so that it is simply always there.
35. As a scientist, I want `/desk` to start the server if it isn't running, so that a cold machine still works on the first try.
36. As a scientist, I want `/desk` to report the desk URL after presenting, so that I know where to look.
37. As a scientist, I want `/desk` to fail loudly if the server can't be reached, so that the agent can't tell me it showed me something it didn't.
38. As a scientist, I want `/desk` to be user-invoked only, so that the model never puts things on my desk on its own initiative.
39. As an agent, I want presenting a figure to be a single command with an optional path, so that I get it right on the first attempt without flag soup.
40. As a scientist, I want the browser to reconnect on its own if the server restarts, so that a stale tab doesn't silently stop updating.

## Implementation Decisions

**Deployment shape.** Single machine. The server, the file store, the watched
source files, and the coding agent all live on `pushkar-studio`. Development
happens there too — building anywhere else would require a deploy step this
design deliberately does not have. Confirmed environment: macOS 27, arm64,
system `python3` 3.9.6, no node, no `uv` yet.

**Runtime.** Python, installed and pinned via `uv` so the server runs in an
isolated environment without touching the system Python on a machine used for
science. Frontend is vanilla JS/CSS with no build step — the page is served
static and talks to the API.

**Process lifetime.** A launchd user agent keeps the server running across
reboots. Studio already runs several launchd agents, so a port that does not
collide with them must be chosen; `7777` is the default subject to that check.

**Network.** Plain HTTP bound to the Tailscale interface, browsed at
`http://pushkar-studio.taila96c04.ts.net:7777`. No TLS, no tokens. Tailscale is
the authentication boundary. HTTPS via `tailscale serve` was considered and
dropped: it existed only for iPad Safari, mobile is out of scope, and the
`tailscale` CLI is not on studio's non-interactive PATH.

**Modules.**

- *Store* — owns the content store and sheet records. Publishing copies the file
  in (never references it in place) and appends a version. Owns trash and
  tombstones. Retains the last 20 versions per sheet; no UI exposes them yet.
- *Layout* — owns desk state: position, size, z-order, pile membership, inbox
  membership, home viewport. Pure state transitions, persisted as JSON.
- *Watcher* — watches source paths that have been published at least once,
  debounced ~300ms so a half-written `savefig` is never ingested mid-write.
- *HTTP API* — publish endpoint, desk state read/write, static assets, SSE stream.
- *Frontend* — canvas rendering and direct manipulation.
- */desk skill* — user-invoked Claude Code skill that resolves the target file
  and publishes it.

**Sheet identity is the absolute source path.** This one rule is load-bearing.
It makes update-in-place the default with zero agent cooperation, and it is what
allowed the watch registry, the drop directory, the flood cap, and the inbox
overflow logic to all be deleted from the design.

**Watching is implicit.** There is no watch registry and no configured
directories. Publishing a path subscribes to that path; nothing else on the
filesystem can ever reach the desk. This makes flooding structurally impossible
rather than something to defend against.

**Publishing is idempotent on path.** Known path → new version on the existing
sheet, position and size untouched. Unknown path → new sheet, placed in the
inbox, never auto-placed on the desk.

**Trash tombstones the path.** A trashed path stops being watched and will not
be re-created by a subsequent file change. An explicit `/desk` on that path
clears the tombstone and brings it back. Zombie sheets were identified as the
single most annoying possible bug in this system.

**Layout authority is exclusively the user's.** The agent cannot specify
position, size, or grouping. There is no auto-tiling and no reflow. The agent's
authority ends at the inbox.

**Rendering.** SVG and PNG as `<img>` — resolution-independent, and twenty
sheets stay smooth where inline SVG would mean 100k DOM nodes for a single dense
scatter. Self-contained HTML in a sandboxed iframe as the escape hatch. Markdown
rendered server-side.

**Live updates over SSE**, not WebSocket — one-way is all that is needed and the
browser reconnects on its own. A sheet update swaps the image silently and
paints a ~600ms highlight ring. No movement, no scroll, no focus change.

**Invocation.** `/desk` is a user-invoked Claude Code skill; its frontmatter
must prevent model invocation. Bare `/desk` resolves the most recently modified
allowlisted figure produced in the session; an optional path overrides. It
auto-starts the server if the port is closed, prints the desk URL, and exits
nonzero on failure. A standalone CLI exists only if the skill genuinely requires
one to do its job — it is not a deliverable in its own right.

**File type allowlist:** `.svg`, `.png`, `.pdf`, `.html`, `.md`. Dotfiles and
`*_tmp*` are ignored.

## Testing Decisions

A good test here asserts external behavior only — what a user or an agent can
observe — and never reaches into internal state. No prior art exists; this is a
greenfield repo, so these tests establish the pattern.

**Two seams, both as high as possible.** The ideal is one; two is the floor
here, because the server and the direct-manipulation UI cannot share a single
honest seam.

*Seam 1 — the HTTP API.* Drive the whole server through it: publish, read desk
state, subscribe to SSE. This covers store, watcher, tombstones, and versioning
without a single unit test below it. Behaviors to cover:

- Publishing an unknown path creates a sheet in the inbox.
- Publishing a known path creates version 2 and leaves position and size untouched.
- Modifying a watched file on disk produces a new version and an SSE event.
- A rapid burst of writes to one file debounces into a single version.
- Publishing copies the file: deleting the source afterwards leaves the sheet intact.
- Trashing tombstones: a subsequent file change does NOT resurrect the sheet.
- An explicit publish of a tombstoned path restores it.
- Version retention caps at 20.
- Disallowed extensions are rejected.

*Seam 2 — the layout model.* Extract desk state transitions (place, move,
resize, pile, unpile, trash, restore, inbox membership, z-order) as pure
functions over a plain state object, tested directly. The DOM layer stays thin
enough to need no tests of its own — this is the deliberate trade that avoids
introducing a browser-automation dependency.

**Not tested:** pixel rendering, drag physics, pan/zoom feel, launchd
integration. These are verified by looking at the thing.

## Out of Scope

Deliberately cut during design, each for a stated reason:

- **Captions, notes, and agent-authored text.** The agent shows a picture; it does not narrate.
- **Agent-authored clusters or grouping.** Layout is the user's alone.
- **Multiple desks.** One desk.
- **Mobile and tablet.** Desktop browser only, despite the iPad being on the tailnet.
- **HTTPS, tokens, passwords, any auth.** Tailscale is the perimeter.
- **MCP server.** Binds to one client for no gain over a shell command.
- **Model-invoked presentation.** User types `/desk`; the model never fires it.
- **Read-back / acknowledgement.** The agent learns nothing about whether the user looked.
- **Annotation and two-way feedback.** Storage should not preclude it later, but no code now.
- **Directory watching, watch registry, drop directory, inbox flood caps.** All obsoleted by implicit path watching.
- **Version-stepping UI.** Versions are stored; no interface exposes them until one is actually wanted.
- **Multi-machine publishing.** Other boxes are compute only; plotting happens on studio.
- **3D and volumetric viewers.**

## Further Notes

**Co-location invariant (settled).** The desk server runs on the same machine as
the coding agent, because the agent and the files it produces are always on one
filesystem and watching is a filesystem operation. Today that machine is studio
for both; the user watches from a separate MacBook over the tailnet, which is
fine — only the *server* and the *files* must be co-located, not the server and
the eyes. Consequence: running an agent on another box (coffee, nairlab-server2)
requires a desk server on that box too. Without one, a sheet publishes once and
then silently stops updating — a failure mode that is hard to notice, since the
stale figure still looks like a figure.

**Accepted trade-off.** Slash-only invocation means an unattended long-running
job cannot leave its result on the desk for the user to find later. Nothing
lands until the user asks. This was chosen deliberately over model invocation.

**Deferred, not rejected.** Two-way annotation — drawing on a figure and having
the agent read the critique back — was identified during design as the genuinely
novel part of the idea and the strongest reason this beats `scp`. It is out of
scope for v1 only because the tool should prove it gets used first. The store
should keep sheet metadata extensible enough that comments can be added as
sidecar records later without migration.

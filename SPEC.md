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
8. As a scientist, I want one page at one URL for all my work, so that there is one place to look — even once that page can show more than one desk (story 42).
9. As a scientist, I want the desk to be a bounded pannable, zoomable slab with visible edges, so that it reads as a real desk seen from above and its edges give me a landmark to navigate by.
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
24a. As a scientist, I want to clear the whole desk in one action, so that starting fresh does not mean trashing sheets one at a time.
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
41. As a scientist, I want to put a figure on a labmate's desk with `/desk path/to/fig.svg --to <their machine>`, so that showing someone a result is one command instead of a file transfer and a message.
42. As a scientist, I want more than one desk — one per paper, say — and to switch between them on the page, so that `/desk` lands on whichever desk I have out and the others keep their layouts untouched.
43. As a scientist, I want to pin a comment on a figure — on a region, a point, or the whole sheet — and see it stay there through new versions until I resolve it, so that what I think is wrong with a plot is recorded where it is wrong, not in a chat I will scroll away from.
44. As a scientist, I want to type `/desk feedback` and have the agent read my comments back — where each one is, in terms it can act on — fix the plotting code, and re-run it so the sheet updates under my comments, so that critiquing a figure is pointing at it rather than describing it, and the fix lands where I am already looking.

## Implementation Decisions

*This section describes the deployment it was written for — one machine,
`pushkar-studio`, on a tailnet. The hostnames are that deployment, not a
requirement: what shipped installs on macOS or Linux, with or without
Tailscale. See `README.md` and `SETUP.md`. The decisions below stand as
recorded.*

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
`http://<machine>.<tailnet>.ts.net:7777`. No TLS, no tokens. Tailscale is
the authentication boundary. HTTPS via `tailscale serve` was considered and
dropped: it existed only for iPad Safari, mobile is out of scope, and the
`tailscale` CLI is not on studio's non-interactive PATH.

**Modules.**

- *Store* — owns the content store and sheet records. Publishing copies the file
  in (never references it in place) and appends a version. Owns trash,
  tombstones, and the comments on a sheet. Retains the last 20 versions per
  sheet; no UI exposes them yet.
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

**Sending to another desk.** `/desk <path> --to <machine>` puts a figure on
someone else's desk. The `desk` command reads the file and hands its bytes to
the desk on that machine over the same publish endpoint; nothing is started or
published locally, and if their desk cannot be reached the command fails
loudly. The sheet lands in the recipient's inbox with an *origin* — the
sending machine's tailnet name — and its identity is the origin plus the
sender's absolute path, so sending the same file again updates that sheet in
place while the same path from two machines stays two sheets. A sent sheet is
never watched: the file it came from is on another disk, and a file at the
same path on the recipient's machine is a different file. A relay that
forwarded every new version from the sender's desk was designed and cut for
v1: it needs a registry of remote subscriptions, fails silently in the
background, and would resurrect a sheet the recipient had thrown away every
time the sender's script re-ran. Sending again is a deliberate act, and that
is what clears the tombstone. The perimeter is unchanged — the bind address,
no tokens — which now means a tailnet peer can put bytes on the desk, not only
name a file already on it. That is the feature, and it is why a desk bound to
localhost cannot receive.

**Several desks, one current.** Desks are named and live one directory each
under `<data>/desks/`; the directory listing is the registry, and
`current.json` names the one that is out. The page shows the current desk
and has a switcher; a `/desk`, a send, and a restore all land on the current
desk, because the command cannot see the page and the user should not have
to say twice which desk they mean. Sheet identity is per desk, so one file
can be a sheet on two desks, and every desk's files stay watched whether or
not it is on screen — a content URL therefore names its desk, since versioned
URLs are cached forever. Only the current desk's events reach the page; the
rest is in the state it fetches on switching. An empty desk can be removed;
the last one cannot. A data directory from before there were desks becomes
the `main` desk on first start, moved, not copied.

**Comments are records on the sheet, not layout.** A comment is `id`, the
`version` it was made on, an `anchor`, `text`, `created_at`, and
`resolved_at`, kept on the sheet record in `sheets.json` — exactly the
sidecar the "Deferred, not rejected" note below asked the store to leave room
for, and it needed no migration. The anchor is a rectangle in fractions of
the content's natural box, a point being a rectangle of zero size and the
whole sheet being no anchor at all; nothing the browser measured is stored,
so a pin lands on the same spot of a new version, and a comment made on a
sheet sent from elsewhere stays here. Iframe kinds take sheet-level comments
only: an iframe eats pointer events and has no stable natural size. A new
version resolves nothing — a pin made on an older version is drawn hollow
with its version number, and when that version is evicted the comment still
names it. Every add, resolve, and remove emits `sheet.changed` carrying the
sheet and no layout, so every open page redraws its pins and nothing can
move. The pins themselves are the one accepted exception to "nothing paints
over a figure": small numbered circles and a thin outline, in fullscreen
only. On the desk a sheet shows only a count of its open comments on its
paper margin. What stayed cut: agent-authored text, replies, freehand (an
agent cannot read a squiggle), and a version-stepping UI.

**Feedback is read, never pushed.** `desk feedback [path] [--all] [--json]`
prints every open comment on one sheet, or on the sheets the agent put up,
with where each one is three ways: a cell of a three-by-three grid in words, the anchor as
fractions, and the anchor in pixels against the current version — and
against the version it was made on while that is retained. The pixels come
from a natural size the server reads from the stored bytes at report time
(PNG from the IHDR chunk; SVG from `width`/`height` when they are plain
pixels, otherwise the `viewBox`, which is the coordinate system an agent
reading the file will find) and carries in the sheet JSON; the command
measures nothing itself, and when there is no size it gives fractions, says
why, and still exits 0. It never starts the server. **Its default scope is what the agent just put
up.** A desk holds many figures from many sessions, and the comments waiting
for an agent are on the ones it made, so bare `desk feedback` has the same
scope as bare `desk present`: the sheets whose files are under the directory
the agent is working in, newest first. A sent sheet is never in that scope,
its path being on another machine. `--all` is the whole desk, for when the
user asks about it. `desk present` says when
the sheet it just presented has open comments, in a third line that names
the command to run, and never when sending with `--to`, because comments
never travel back to the sender. The `/desk` skill is where this becomes a
loop: on that third line the agent runs `desk feedback` before anything
else, restates each comment as one concrete change to the plotting code,
makes it, re-runs the script so the watched sheet updates in place, and
tells the user to look and resolve what is fixed. A sheet with an origin is
a copy whose script is elsewhere, so its feedback is reported, not acted on.
What stayed cut: read-back in the other direction (the agent never learns
whether the user looked), anything model-invoked, and agent-authored text —
the agent reads comments and never writes, resolves, removes, or replies to
one. Layout authority stays the user's; so does the last word on a figure.

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

**The desk is bounded.** 4800 × 3200 desk units on a featureless floor.
Ticket 07 built an infinite canvas and story 9 originally asked for one, on the
reasoning that the user should never run out of room. That was reversed once
the desk became literal: a surface seen from directly above has edges, and the
coffee has to stand on something. Panning stops with at most 160px of floor
showing, with no rubber-band — a bounce would imply the desk moved on its own,
and the grammar of this app is that nothing moves unless the user moves it.
Sheets clamp to the slab; paper does not hang off a desk. The accepted cost is
that a full desk must be curated rather than escaped from.

**Two skins, one structure.** The desk comes in `day` — an oak table in a
coffee shop, on a warm terrazzo floor, under flat window light — and `night` —
dark walnut under a single warm lamp that is a real object at a fixed spot on
the desk, so panning moves the pool with the wood. `desk.css` owns structure
and holds no colour of its own; `skins.css` holds nothing but colour, light,
and which props are on. Switching skins therefore cannot move a sheet or change
a gesture. The choice lives in `localStorage` and the server never learns about
it. A third skin was designed and cut for not being good enough.

**One material rule governs every skin: nothing paints over a figure.** Warm
light lives inside `#desktop`, which is behind every sheet, and every material
cue on a sheet lands on its 3px paper margin. A sheet in an unlit corner is not
dimmed — only its surround is. That is physically wrong and deliberate: the
figures are the point.

**The coffee is a photograph.** Drawn gradients could not make a convincing
crema or latte art at the size the cup is displayed, so the coffee is a
public-domain photograph masked to the cup's rim circle, and CSS draws the
ceramic rim and handle around it. It is the only binary asset in the repo and
`web/ASSETS.md` records its source, licence, and processing. Each skin grades
the same cup into its own light, which is also how the cup goes cold.

**Connection state is the coffee, not a banner.** A mug at the near-right
corner steams while the SSE stream is live; when it drops the steam wafts away
over a second — a one-second blip must not flash at the user — and the coffee
goes cold and still. This is a quieter alarm than the red banner it replaces,
so an outage lasting 20s escalates the cup, and the same fact is carried in
words on an `aria-live` region throughout.

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
- **Multiple desks** was cut here as "one desk", and reversed on request: story 42. What stays cut is any coupling between desks — no moving a sheet from one to another, no desk-of-desks, no per-desk address for sending.
- **Mobile and tablet.** Desktop browser only, despite the iPad being on the tailnet.
- **HTTPS, tokens, passwords, any auth.** Tailscale is the perimeter.
- **MCP server.** Binds to one client for no gain over a shell command.
- **Model-invoked presentation.** User types `/desk`; the model never fires it.
- **Read-back / acknowledgement.** Cut as "the agent learns nothing about whether the user looked", and reversed in one direction only: story 44. The agent can read the user's comments when the user asks it to (`/desk feedback`, or a `desk present` that reports open comments). What stays cut: the agent still learns nothing about whether the user looked, nothing reaches it unasked, and it cannot acknowledge, resolve, or reply.
- **Annotation and two-way feedback.** Cut as "storage should not preclude it later, but no code now", and reversed once the desk had proved it gets used: story 43. What stays cut is agent-authored text of any kind — the agent never writes, replies to, resolves, or removes a comment — freehand drawing, and painting over a figure, except for the pins themselves.
- **Directory watching, watch registry, drop directory, inbox flood caps.** All obsoleted by implicit path watching.
- **Version-stepping UI.** Versions are stored; no interface exposes them until one is actually wanted.
- **Cross-machine watching.** A desk watches files on its own disk only. Sending (`--to`) puts a *copy* on another desk, updated when it is sent again and not before; the relay that would forward every version was cut as the zombie the trash exists to prevent. This narrows the original cut of "multi-machine publishing", reversed for one case: one person's figure on another person's desk.
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
stale figure still looks like a figure. The one way across is `--to`: a copy,
sent by hand, that says on its face where it came from, so a stale one at
least looks like what it is.

**Accepted trade-off.** Slash-only invocation means an unattended long-running
job cannot leave its result on the desk for the user to find later. Nothing
lands until the user asks. This was chosen deliberately over model invocation.

**Deferred, not rejected — and then built.** Two-way annotation — drawing on a
figure and having the agent read the critique back — was identified during
design as the genuinely novel part of the idea and the strongest reason this
beats `scp`. It was out of scope for v1 only because the tool should prove it
gets used first, and the store was asked to keep sheet metadata extensible
enough that comments could be added as sidecar records later without
migration. It did, and they were: comments are records on the sheet (story
43), added without a migration. "Drawing" became a rectangle or a point, not
freehand, because an agent can act on a box and cannot act on a squiggle.

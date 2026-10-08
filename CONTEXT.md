# Context

Domain glossary for Desk. These words mean exactly this and nothing else. Use
them in code, tests, commits, and tickets; do not introduce synonyms.

## Nouns

**Desk** — a bounded pannable, zoomable slab, 4800 × 3200 desk units, seen
from directly above. Not a board, not a canvas, not a workspace. A user may
keep several, each with a name, and exactly one of them is **current**: the
one the page shows and the one everything lands on. The first is called
`main`.

**Sheet** — one figure on the desk. A sheet has a position, a size, a z-order,
and an ordered list of versions. It is the unit the user drags, resizes, piles,
and trashes.

**Version** — one revision of a sheet's content. Publishing the same source path
again appends a version; it never creates a second sheet. The last 20 are
retained.

**Comment** — a note the user pins on a sheet, in the fullscreen view: on a
rectangle, on a point, or on the whole sheet. It is a record on the sheet,
not layout, and it names the version it was made on. A comment is **open**
until the user resolves it; it rides with the sheet through new versions,
the trash, and a restart. Only the user writes one.

**Feedback** — the open comments on a sheet, read back by the agent with
`desk feedback`: where each one is, in fractions, in pixels, and in words,
and what it says. Feedback reaches the agent only when the user asks for it.
Resolved comments are not feedback.

**Store** — the desk's own copy of every published file. Publishing copies the
file in. The desk never reads the user's original file at render time, so
deleting or clobbering the source cannot damage a sheet.

**Source path** — the absolute filesystem path a sheet was published from. This
is the sheet's *identity*: same path means same sheet, forever.

**Origin** — the machine a sheet was sent from, when that is not this one,
named by its tailnet machine name, or its hostname when it has no tailnet. A
sheet with an origin is a copy: this desk
cannot watch the file it came from, and only the next send from the same
origin adds a version. A sheet published here has no origin.

**Inbox** — the strip at the edge of the desk where new sheets land. A sheet in
the inbox has not been placed. Nothing leaves the inbox except by the user
dragging it out.

**Pile** — a user-formed stack of sheets. Created by dragging one sheet onto
another, fanned open by clicking, disassembled by pulling a sheet out.

**Tombstone** — the record that a source path was trashed. A tombstoned path is
no longer watched and will not be re-created by a file change. An explicit
publish clears it.

**Floor** — what lies past the desk's edge. Nothing is ever placed on the
floor; it exists so that an edge reads as an edge.

**Skin** — one of the desk's materials: `day` or `night`. A skin sets colour,
light, and which props are present, and nothing else. It cannot move a sheet,
change a gesture, or alter the desk's geometry. Which skin is showing is a
property of the browser, not of the desk, so it never reaches the server.

**Home** — a desk's origin viewport, restored by the `0` key. Each desk has
its own.

## Verbs

**Publish** — hand a file to the desk. Known source path → new version on the
existing sheet, position and size untouched. Unknown source path → new sheet in
the inbox.

**Present** — what the *user* does by typing `/desk`. Resolves a file, then
publishes it. The user presents; the system publishes.

**Send** — present a file to someone else's desk: `/desk fig.svg --to <their
machine>`. The file's bytes travel; the receiving desk publishes them as a
sheet whose identity is the origin plus the source path there, so sending the
same file again updates that sheet in place. The sender's authority ends at the
recipient's inbox, exactly as an agent's does.

**Watch** — observe a source path for changes. Watching is implicit: publishing
a path subscribes to it. There is no watch registry, no configured directory,
and no way for an unpublished file to reach the desk.

**Place** — move a sheet from the inbox onto the desk. Only the user places.
Nothing auto-places, auto-tiles, or reflows.

**Switch** — make another desk current. Only the user switches, on the page.
Every desk stays watched whether or not it is current; switching changes
where the next sheet lands and what is on screen, and nothing else.

**Resolve** — what the *user* does to a comment that has been dealt with. The
comment stays on the sheet, closed. Only the user resolves, and only the user
removes; the agent never does either.

## Load-bearing rules

1. **Sheet identity is the absolute source path, on the machine the file lives
   on.** A sheet published here is its path; a sheet sent from elsewhere is its
   origin plus its path there. This one rule is what makes update-in-place the
   default with zero agent cooperation, and it is why the watch registry, drop
   directory, flood cap, and inbox overflow logic do not exist.

2. **Layout authority is exclusively the user's.** The agent cannot specify
   position, size, or grouping. Its authority ends at the inbox. The agent
   may read feedback, but it still cannot put text on the desk: it never
   writes, resolves, or removes a comment.

3. **Updates never steal attention.** A sheet updating swaps its image and
   paints a brief highlight ring. No movement, no scroll, no focus change, no
   reflow.

4. **The server runs on the same machine as the agent.** Watching is a
   filesystem operation. The *user* may watch from anywhere on the tailnet. A
   sent sheet is a copy of a file on another machine, which is exactly why it
   is never watched.

5. **The desk has edges.** Panning stops at them and sheets clamp to the slab;
   nothing is ever placed on the floor. This replaced the infinite canvas
   deliberately — an edge is a landmark, which a featureless infinite plane
   never gave us. The cost is real and accepted: a full desk must be curated
   with piles and the trash, not escaped from.

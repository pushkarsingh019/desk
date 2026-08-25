# Context

Domain glossary for Desk. These words mean exactly this and nothing else. Use
them in code, tests, commits, and tickets; do not introduce synonyms.

## Nouns

**Desk** — the single infinite pannable, zoomable canvas. There is exactly one.
Not a board, not a canvas, not a workspace.

**Sheet** — one figure on the desk. A sheet has a position, a size, a z-order,
and an ordered list of versions. It is the unit the user drags, resizes, piles,
and trashes.

**Version** — one revision of a sheet's content. Publishing the same source path
again appends a version; it never creates a second sheet. The last 20 are
retained.

**Store** — the desk's own copy of every published file. Publishing copies the
file in. The desk never reads the user's original file at render time, so
deleting or clobbering the source cannot damage a sheet.

**Source path** — the absolute filesystem path a sheet was published from. This
is the sheet's *identity*: same path means same sheet, forever.

**Inbox** — the strip at the edge of the desk where new sheets land. A sheet in
the inbox has not been placed. Nothing leaves the inbox except by the user
dragging it out.

**Pile** — a user-formed stack of sheets. Created by dragging one sheet onto
another, fanned open by clicking, disassembled by pulling a sheet out.

**Tombstone** — the record that a source path was trashed. A tombstoned path is
no longer watched and will not be re-created by a file change. An explicit
publish clears it.

**Home** — the desk's origin viewport, restored by the `0` key.

## Verbs

**Publish** — hand a file to the desk. Known source path → new version on the
existing sheet, position and size untouched. Unknown source path → new sheet in
the inbox.

**Present** — what the *user* does by typing `/desk`. Resolves a file, then
publishes it. The user presents; the system publishes.

**Watch** — observe a source path for changes. Watching is implicit: publishing
a path subscribes to it. There is no watch registry, no configured directory,
and no way for an unpublished file to reach the desk.

**Place** — move a sheet from the inbox onto the desk. Only the user places.
Nothing auto-places, auto-tiles, or reflows.

## Load-bearing rules

1. **Sheet identity is the absolute source path.** This one rule is what makes
   update-in-place the default with zero agent cooperation, and it is why the
   watch registry, drop directory, flood cap, and inbox overflow logic do not
   exist.

2. **Layout authority is exclusively the user's.** The agent cannot specify
   position, size, or grouping. Its authority ends at the inbox.

3. **Updates never steal attention.** A sheet updating swaps its image and
   paints a brief highlight ring. No movement, no scroll, no focus change, no
   reflow.

4. **The server runs on the same machine as the agent.** Watching is a
   filesystem operation. The *user* may watch from anywhere on the tailnet.

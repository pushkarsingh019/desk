# 18: Comments on a sheet

**What to build:** The user pins a *comment* on a figure. In the fullscreen
view a button in the bar, or the `c` key, enters comment mode; drag a
rectangle or click a point, type in a small textarea, Enter saves. A comment is
open until the user resolves it, and it rides with the sheet through new
versions, the trash, and a restart. In fullscreen the pins are drawn, numbered,
over the figure; on the desk a sheet shows only a small count of its open
comments. Nothing is read back to the agent yet; that is ticket 19.

**Blocked by:** 17

**Status:** done

Reverses the "Annotation and two-way feedback" cut in SPEC.md, which was
marked deferred, not rejected: the store kept sheet records extensible so that
comments could be added later without a migration, and this is that. What
stays cut: agent-authored text, since the agent never writes, replies to, or
resolves a comment; freehand, since an agent cannot read a squiggle, so an
anchor is a rectangle or a point and nothing else; version stepping, since a
pin made on an older version says which version and that is all; and painting
over a figure — except for the pins themselves, which are small.

- [x] A comment is a record on the sheet in `sheets.json`, not layout state: `id`, the `version` it was made on, `anchor`, `text`, `created_at`, and `resolved_at` or null; nothing the browser measured is stored
- [x] An anchor is a rectangle in fractions `0..1` of the content's natural box (`x`, `y`, `w`, `h`); a point is a rectangle of zero size; a sheet-level comment has `anchor: null`
- [x] `POST /api/comments` takes `{"op": "add" | "resolve" | "remove", ...}` on the current desk, mirroring `/api/layout`: `add` takes `sheet_id`, `anchor`, and `text`, and the server stamps the sheet's current version on it; `resolve` and `remove` take `sheet_id` and `comment_id`
- [x] `add` is refused with 400 and a clear error for an unknown sheet, empty or non-string text, a fraction outside `0..1`, or a rectangle that leaves the box; `resolve` and `remove` of an unknown comment are 400 too
- [x] An anchor on an `html`, `md`, or `pdf` sheet is refused with 400: iframe kinds take sheet-level comments only, because an iframe eats pointer events and has no stable natural size
- [x] Sheet JSON in `/api/state`, and in every event that carries a sheet, has `comments` (every comment, open and resolved, in the order made) and `open_comments` (the count)
- [x] A new version resolves nothing: the comment keeps its version number and the pin stays at the same fractional spot over the new image
- [x] Comments survive trash and restore, a server restart, and the eviction of the version they were made on (`MAX_VERSIONS`): the comment stays, still naming its version
- [x] A comment can be made on a sent sheet (one with an origin) and never travels back to the sender; one file on two desks has two independent comment sets
- [x] A damaged comment record is dropped on load and the sheet still starts with the rest, as a damaged version is
- [x] Every add, resolve, and remove emits `sheet.changed` carrying the sheet JSON and no layout, so every open page redraws its pins and nothing can move
- [x] In fullscreen, a **comment** button in the bar and the `c` key enter comment mode; drag a rectangle or click a point, type in a small textarea, Enter saves, Escape cancels the comment in progress (a second Escape closes fullscreen as before)
- [x] Pins are numbered; a pin made on an older version renders hollow with its version number; clicking a pin shows its text with **resolve** and **remove**
- [x] On the desk a sheet shows only a small open-comment count on its paper margin, never over the figure; the fullscreen pins are the one accepted exception to "nothing paints over a figure"
- [x] `CONTEXT.md` gets the noun **Comment** and the user-only verb **Resolve**; `SPEC.md` amends (not deletes) the "Annotation and two-way feedback" cut and the "Deferred, not rejected" note, adds a user story, and adds an Implementation Decisions paragraph; `README.md` lists the gesture
- [x] Tested through the HTTP API seam in `tests/test_api.py`: add and read back; bound to the version current at the time; survives a new version, trash and restore, a restart, and eviction; resolve and remove; a comment with no anchor; bad input rejected with 400. Comments are not layout, so there is no third seam and `tests/test_layout.py` is untouched

# 04: Implicit watching

**What to build:** The payoff behaviour. A user presents a figure once; from
then on, re-running the plotting script updates the sheet with no second
command. Publishing a path is what subscribes to it — there is no watch
registry, no configured directory, and no way for an unpublished file to reach
the desk.

**Blocked by:** 03

**Status:** ready-for-agent

- [x] Publishing a source path begins watching that path
- [x] Modifying a watched file on disk creates a new version and emits an SSE event, with no second publish call
- [x] A rapid burst of writes to one file debounces into a single version (roughly 300ms), so a half-written `savefig` is never ingested mid-write
- [x] A file that has never been published is never picked up, no matter where it is written
- [x] Watches survive a server restart
- [x] Deleting a watched source file does not delete or damage the sheet
- [x] All behaviour is tested through the HTTP API seam

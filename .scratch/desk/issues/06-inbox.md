# 06: Inbox

**What to build:** New sheets land in a strip at the edge of the desk and stay
there until the user drags them out. Nothing ever auto-places, auto-tiles, or
covers something the user positioned by hand.

**Blocked by:** 02, 05

**Status:** ready-for-agent

- [x] A newly created sheet appears in the inbox strip, never on the desk surface
- [x] Dragging a sheet out of the inbox places it on the desk at the drop position
- [x] A placed sheet is never returned to the inbox by any subsequent event
- [x] A new version of a sheet already placed on the desk updates it in place and does not send it back to the inbox
- [x] No code path places, tiles, or repositions a sheet without a user gesture
- [x] Inbox membership is part of the layout state and persists across restart

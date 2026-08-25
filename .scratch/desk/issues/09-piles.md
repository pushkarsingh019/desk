# 09: Piles

**What to build:** Shoving a group of sheets aside becomes one gesture instead
of many. The user drags one sheet onto another to stack them, clicks to fan the
stack open, and pulls a sheet back out when needed.

**Blocked by:** 06

**Status:** ready-for-agent

- [x] Dragging one sheet onto another forms a pile showing a count
- [x] Clicking a pile fans it open so its contents are visible
- [ ] Clicking again, or clicking away, collapses it
- [x] The user can drag a single sheet out of a pile, leaving the rest intact
- [x] A pile of two, reduced to one, stops being a pile
- [x] A pile moves as a unit when dragged
- [x] A sheet inside a pile still updates in place when it gains a version
- [x] Pile membership is part of the layout state, tested as pure transitions, and persists across restart

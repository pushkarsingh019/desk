# 08: Fullscreen enlarge

**What to build:** Double-clicking a sheet blows it up fullscreen so a dense
figure can actually be read, with its own pan and zoom — and dismissing it
returns the desk exactly as it was.

**Blocked by:** 07

**Status:** ready-for-agent

- [x] Double-clicking a sheet opens it fullscreen over the desk
- [x] The fullscreen view has its own pan and zoom, independent of the desk viewport
- [x] Escape and a visible control both dismiss it
- [x] Dismissing restores the desk's layout and viewport exactly as they were — enlarging is non-destructive
- [x] A sheet updating while fullscreen swaps its image in place, consistent with ticket 03

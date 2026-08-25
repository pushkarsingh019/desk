# 05: Layout model — drag, resize, persist

**What to build:** Sheets go where the user puts them and stay there. This
ticket establishes the second test seam: layout transitions as pure functions
over a plain state object.

**Blocked by:** 01

**Status:** ready-for-agent

- [ ] A layout module exposes desk state — position, size, z-order — as a plain object, with transitions as pure functions over it
- [ ] The user can drag a sheet to any position on the desk
- [ ] The user can resize a sheet by dragging a corner
- [ ] Clicking a sheet raises it in z-order
- [ ] Layout persists to disk as JSON and is restored on server start, so the desk survives a reboot
- [ ] Layout transitions are tested directly as pure functions, with no DOM involved
- [ ] The DOM layer is thin enough to need no tests of its own

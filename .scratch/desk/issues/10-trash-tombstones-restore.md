# 10: Trash, tombstones, restore

**What to build:** Throwing a sheet away means it stays away. A trashed source
path stops being watched and cannot be resurrected by a later file change —
zombie sheets were identified during design as the single most annoying possible
bug in this system.

**Blocked by:** 04, 06

**Status:** ready-for-agent

- [x] The user can throw a sheet away from the desk, the inbox, or a pile
- [x] Trashing stops watching that source path
- [x] Modifying the source file of a trashed sheet does NOT bring it back
- [x] An explicit publish of a tombstoned path clears the tombstone and restores the sheet to the inbox
- [x] A trash corner lists discarded sheets and can restore one, with its content intact
- [x] Tombstones persist across restart
- [x] All server-side behaviour is tested through the HTTP API seam; layout-side removal is tested as a pure transition

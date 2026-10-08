# 17: Several desks, one current

**What to build:** More than one desk. Each is named and keeps its own sheets,
layout, and trash; exactly one is *current*. The page shows the current desk
and has a switcher. Everything that arrives — a `/desk`, a send, a restore —
lands on the current desk. The others keep watching their files and are
exactly as the user left them when switched back to.

**Blocked by:** 16

**Status:** done

Reverses the "one desk" cut in SPEC.md at the user's request. What stays cut
is any coupling between desks: no moving a sheet across, no aiming a present
at a desk that is not out.

- [x] Desks live one directory each under `<data>/desks/`; a data directory from before there were desks becomes `main` on first start
- [x] `POST /api/desks` creates (and brings out), switches, and removes; the current desk survives a restart
- [x] A `/desk`, a send, and a restore land on the current desk; switching back shows the other desk untouched
- [x] Sheet identity is per desk; one file can be a sheet on two desks and both keep updating, and their content URLs never collide
- [x] Only the current desk's events reach the page; switching emits `desk.changed` and every open page follows
- [x] An empty desk can be removed, never one with sheets, never the last one
- [x] A desk name is refused if it would not survive a URL or a directory
- [x] The page has a switcher, `+ desk`, and `remove`; `desk status` names the desk that is out
- [x] `CONTEXT.md`, `SPEC.md`, `README.md`, `SETUP.md`, and the skill say all of this
- [x] Tested through the HTTP API seam and the `desk` command

# 02: Sheet identity and versions

**What to build:** Publishing the same file twice updates one sheet instead of
creating two. A sheet accumulates versions, and its identity is the absolute
source path it was published from.

**Blocked by:** 01

**Status:** ready-for-agent

- [x] Publishing an unknown source path creates a new sheet at version 1
- [x] Publishing a known source path appends version 2 to the existing sheet and creates no second sheet
- [x] A sheet's position and size are untouched by a new version
- [x] Version history retains the last 20 versions per sheet; the 21st evicts the oldest
- [x] The file extension allowlist (`.svg`, `.png`, `.pdf`, `.html`, `.md`) is enforced; anything else is rejected with a clear error
- [x] Dotfiles and paths matching `*_tmp*` are rejected
- [x] All behaviour is tested through the HTTP API seam

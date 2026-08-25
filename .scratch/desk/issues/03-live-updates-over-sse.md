# 03: Live updates over SSE

**What to build:** An open desk page updates itself. When a sheet gains a new
version, the page swaps the image with a brief highlight ring — and does not
move, scroll, or steal focus. This is the behaviour that lets a user stare at
one spot while an agent iterates.

**Blocked by:** 02

**Status:** ready-for-agent

- [ ] The server exposes an SSE stream that emits an event when a sheet is created or gains a version
- [ ] The page subscribes on load and applies events without a reload
- [ ] A new version swaps the sheet's image in place — position, size, and scroll position are provably unchanged
- [ ] The swap paints a highlight ring for roughly 600ms and nothing else
- [ ] No update moves any element, scrolls the page, changes focus, or reflows the layout
- [ ] The page reconnects on its own after the server restarts, without a manual reload
- [ ] The SSE stream is tested through the HTTP API seam

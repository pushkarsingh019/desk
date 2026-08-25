# 01: Walking skeleton — publish a figure, see it on a page

**What to build:** A user can hand an SVG or PNG file to the desk and then see
it rendered in a browser. This is the tracer bullet: a complete path from
publish through store to a rendered page, with the HTTP-API test seam
established.

**Blocked by:** None (can start immediately)

**Status:** ready-for-agent

- [ ] A `uv`-managed Python project runs a server on a chosen port, with a documented command to start it in the foreground
- [ ] A publish endpoint accepts a file and its absolute source path, and copies the file into the desk's own store
- [ ] The desk page lists all sheets and renders SVG and PNG sheets as `<img>` elements
- [ ] Publishing a file and then deleting the source file leaves the sheet rendering correctly (proves the store copies, not references)
- [ ] A state endpoint returns the current sheets as structured data
- [ ] Tests drive the server through the HTTP API only, with no test reaching beneath it
- [ ] The port does not collide with the launchd agents already running on studio; the chosen port is recorded in the README

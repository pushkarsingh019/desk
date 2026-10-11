# 20: Download a sheet

**What to build:** A **download** button in the fullscreen bar, beside
**feedback**, that hands the user the store's copy of the sheet they are
looking at: the bytes exactly as they were published, under the file's own
name. No re-render, no screenshot, no resampling — the SVG is the SVG, the
PNG is the PNG, the PDF is the PDF. Fullscreen only ever shows the latest
version, so that is what the button downloads; the version-stepping UI stays
cut.

**Blocked by:** 08

**Status:** done

A sheet with an origin is a copy, and a copy is exactly what the user wants
to take away, so the button shows for every sheet; it is the feedback button
that hides on a sent sheet, because the script is elsewhere, and that rule
is not copied here.

- [x] Every sheet's JSON carries a `download_url` next to its `content_url`: the same versioned content URL with `?download`
- [x] `GET /api/content/<desk>/<id>/<version>?download` serves the stored bytes untouched with `Content-Disposition: attachment` naming the source file; markdown is the markdown (`text/markdown`), not the rendered page, and an unreadable file is its own bytes, not the error page
- [x] The name survives whatever it is: an ASCII fallback in `filename`, the real name percent-encoded in `filename*` (RFC 6266), so a non-ASCII or quoted name never breaks the header
- [x] The versioned download URL keeps the immutable cache rule; an unknown sheet or version is a 404
- [x] The page's button reads the sheet's `download_url` at click time and navigates through a temporary anchor with the `download` attribute, so a version arriving while the view is open downloads what is on screen
- [x] `SPEC.md` gets story 46 and an Implementation Decisions paragraph; `README.md` gets a row in the gesture table
- [x] Tested through the API in `tests/test_api.py`: SVG and PNG byte-identical with the attachment header; markdown raw; an older version by number; a non-ASCII name; 404s

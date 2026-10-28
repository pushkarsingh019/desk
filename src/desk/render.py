"""Server-side rendering for the sheet kinds a browser cannot show directly.

Markdown becomes a self-contained HTML document. So does the failure case: a
file whose extension is allowed but whose content is unreadable renders as a
visible error *on that sheet*, rather than breaking the desk.

A PDF becomes a PNG. The browser's own PDF viewer would show it, but only in
an iframe, and an iframe eats pointer events and has no natural box, so a
PDF shown that way could take no pinned comment. Rendered here, a PDF is an
image like any other: it has a natural box, in the PDF's own points, and a
comment's fractions are fractions of that.
"""

from __future__ import annotations

import html as html_escape
import struct
import threading
import zlib

import markdown as markdown_lib
import pypdfium2 as pdfium

_PAGE = """<!doctype html>
<html><head><meta charset="utf-8">
<style>
:root {{ color-scheme: light; }}
body {{
  margin: 0; padding: 18px 20px;
  font: 14px/1.6 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  color: #1c1c1e; background: #fff;
}}
h1, h2, h3, h4 {{ line-height: 1.25; margin: 1.2em 0 .5em; }}
h1 {{ font-size: 1.6em; }} h2 {{ font-size: 1.3em; }} h3 {{ font-size: 1.1em; }}
p, ul, ol, blockquote, table {{ margin: 0 0 1em; }}
img {{ max-width: 100%; }}
pre, code {{
  font: 12px/1.5 ui-monospace, SFMono-Regular, Menlo, monospace;
  background: #f4f4f6; border-radius: 6px;
}}
code {{ padding: 1px 5px; }}
pre {{ padding: 12px 14px; overflow-x: auto; }}
pre code {{ padding: 0; background: none; }}
blockquote {{ margin-left: 0; padding-left: 14px; border-left: 3px solid #d0d0d4; color: #6a6a70; }}
table {{ border-collapse: collapse; }}
table th, table td {{ border: 1px solid #d8d8dc; padding: 5px 10px; text-align: left; }}
.desk-unreadable {{
  font: 13px/1.5 ui-monospace, SFMono-Regular, Menlo, monospace;
  color: #b3261e; background: #fdecea; border: 1px solid #f3b8b2;
  border-radius: 8px; padding: 14px 16px;
}}
</style></head>
<body>{body}</body></html>
"""


def markdown_to_page(data: bytes) -> bytes:
    """Render a markdown file to a self-contained HTML document."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        return unreadable_page("This markdown file is not valid UTF-8", str(exc))
    body = markdown_lib.markdown(
        text, extensions=["extra", "sane_lists", "toc", "codehilite"]
    )
    return _PAGE.format(body=body).encode("utf-8")


def unreadable_page(headline: str, detail: str = "") -> bytes:
    """A visible failure for one sheet, rendered inside that sheet's frame."""
    body = f'<div class="desk-unreadable"><strong>{html_escape.escape(headline)}</strong>'
    if detail:
        body += f"<br>{html_escape.escape(detail)}"
    body += "</div>"
    return _PAGE.format(body=body).encode("utf-8")


# -- PDF ------------------------------------------------------------------

#: pdfium is not safe to drive from several threads at once, and the server
#: answers content requests concurrently, so every call into it is serialised.
_PDFIUM = threading.Lock()

#: Pages are stacked top to bottom with this much paper between them, in
#: points. One page — the usual figure — has no gap at all.
PAGE_GAP_PT = 18

#: Device pixels per point at the preferred rendering scale: 2 is crisp on a
#: retina display at 100%.
PDF_SCALE = 2.0

#: The longest side of the rendered image, in pixels, and its area. A poster
#: or a long manuscript is scaled down to fit under these rather than asking a
#: browser to decode a gigapixel PNG.
MAX_PDF_SIDE = 8192
MAX_PDF_AREA = 24_000_000


class PdfUnreadable(ValueError):
    """The bytes are not a PDF pdfium can open, or it has no pages."""


def pdf_box(data: bytes) -> dict:
    """The natural box of a PDF in points: the widest page by the pages
    stacked, with `PAGE_GAP_PT` between them. Raises PdfUnreadable."""
    with _PDFIUM:
        document = _open(data)
        try:
            sizes = [document[i].get_size() for i in range(len(document))]
        finally:
            document.close()
    return _box(sizes)


def pdf_to_png(data: bytes) -> bytes:
    """Rasterise every page of a PDF, stacked top to bottom, into one PNG."""
    with _PDFIUM:
        document = _open(data)
        try:
            sizes = [document[i].get_size() for i in range(len(document))]
            box = _box(sizes)
            scale = min(
                PDF_SCALE,
                MAX_PDF_SIDE / box["w"],
                MAX_PDF_SIDE / box["h"],
                (MAX_PDF_AREA / (box["w"] * box["h"])) ** 0.5,
            )
            width = max(1, round(box["w"] * scale))
            height = max(1, round(box["h"] * scale))
            # White paper, then each page drawn into its own band.
            row = b"\xff" * (width * 3)
            rows = [bytearray(row) for _ in range(height)]
            top = 0.0
            for i, (pw, ph) in enumerate(sizes):
                page = document[i]
                bitmap = page.render(scale=scale, rev_byteorder=True, prefer_bgrx=False)
                try:
                    raw = bytes(bitmap.buffer)
                    y0 = round(top * scale)
                    for y in range(bitmap.height):
                        if y0 + y >= height:
                            break
                        start = y * bitmap.stride
                        line = raw[start : start + bitmap.width * 3]
                        rows[y0 + y][: len(line)] = line
                finally:
                    bitmap.close()
                top += ph + PAGE_GAP_PT
        finally:
            document.close()
    return _png(width, height, rows)


def _open(data: bytes):
    try:
        document = pdfium.PdfDocument(data)
    except Exception as exc:  # pdfium raises its own error type
        raise PdfUnreadable(f"not a PDF the desk can open: {exc}") from exc
    if len(document) == 0:
        document.close()
        raise PdfUnreadable("this PDF has no pages")
    return document


def _box(sizes) -> dict:
    w = max(pw for pw, _ in sizes)
    h = sum(ph for _, ph in sizes) + PAGE_GAP_PT * (len(sizes) - 1)
    if not (w > 0 and h > 0):
        raise PdfUnreadable("this PDF has a page of no size")
    return {"w": _tidy(w), "h": _tidy(h)}


def _tidy(number: float):
    return int(number) if number == int(number) else round(number, 2)


def _png(width: int, height: int, rows) -> bytes:
    """Encode 8-bit RGB rows as a PNG with the standard library alone."""
    def chunk(kind: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))

    raw = b"".join(b"\x00" + bytes(r) for r in rows)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 6))
        + chunk(b"IEND", b"")
    )

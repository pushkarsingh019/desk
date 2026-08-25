"""Server-side rendering for the sheet kinds a browser cannot show directly.

Markdown becomes a self-contained HTML document. So does the failure case: a
file whose extension is allowed but whose content is unreadable renders as a
visible error *on that sheet*, rather than breaking the desk.
"""

from __future__ import annotations

import html as html_escape

import markdown as markdown_lib

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

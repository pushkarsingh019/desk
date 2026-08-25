# 11: Remaining renderers — HTML, markdown, PDF

**What to build:** The escape hatch. Anything the desk cannot render natively,
an agent can render to a self-contained HTML file and present instead.

**Blocked by:** 01

**Status:** ready-for-agent

- [x] A self-contained HTML plot (plotly, bokeh, altair) renders in a sandboxed iframe and stays interactive
- [x] The iframe sandbox prevents the embedded page from reaching or altering the desk
- [x] A markdown file renders as a readable sheet
- [x] A PDF displays as a sheet
- [x] Each type resizes, drags, piles, and updates in place exactly like an image sheet
- [x] A file whose extension is allowed but whose content is unreadable fails visibly on that sheet rather than breaking the desk

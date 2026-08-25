# 13: The `/desk` skill

**What to build:** The user types `/desk` in Claude Code and the figure the
agent just made appears on the desk. This is the entire user-facing interface.

**Blocked by:** 12

**Status:** ready-for-agent

- [ ] `/desk` with no arguments resolves the most recently modified allowlisted figure produced in the session and presents it
- [ ] `/desk <path>` presents that file specifically
- [ ] The skill's frontmatter makes it **user-invoked only** — the model must never fire it on its own initiative
- [ ] If the server is not running, the skill starts it and waits for the port before presenting, so a cold machine works on the first try
- [ ] The skill prints the desk URL on success
- [ ] The skill exits nonzero and reports clearly on failure, so a failed present is never reported as a success
- [ ] Presenting a path that is already a sheet updates it in place rather than creating a duplicate
- [ ] Installation instructions cover placing the skill where Claude Code on studio will find it

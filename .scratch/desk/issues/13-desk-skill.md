# 13: The `/desk` skill

**What to build:** The user types `/desk` in Claude Code and the figure the
agent just made appears on the desk. This is the entire user-facing interface.

**Blocked by:** 12

**Status:** ready-for-agent

- [x] `/desk` with no arguments resolves the most recently modified allowlisted figure produced in the session and presents it
- [x] `/desk <path>` presents that file specifically
- [x] The skill's frontmatter makes it **user-invoked only** — the model must never fire it on its own initiative
- [x] If the server is not running, the skill starts it and waits for the port before presenting, so a cold machine works on the first try
- [x] The skill prints the desk URL on success
- [x] The skill exits nonzero and reports clearly on failure, so a failed present is never reported as a success
- [x] Presenting a path that is already a sheet updates it in place rather than creating a duplicate
- [x] Installation instructions cover placing the skill where Claude Code on studio will find it

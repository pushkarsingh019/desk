# Installing the Desk skill

`scripts/install.sh` links this directory into every agent on the machine that
has a skills directory:

```
~/.claude/skills/desk    -> <project>/skill/desk
~/.pi/agent/skills/desk  -> <project>/skill/desk
~/.agents/skills/desk    -> <project>/skill/desk
~/.codex/skills/desk     -> <project>/skill/desk
```

A directory whose parent does not exist is skipped, so you get links only for
the agents you actually have. If your agent keeps skills somewhere else, name
it and re-run the installer:

```
DESK_SKILL_DIRS="$HOME/.myagent/skills/desk" sh scripts/install.sh
```

One canonical copy, in the repo, beside the server it drives. Editing
`SKILL.md` updates every agent at once, and the skill can never describe a
`desk` command the installed server does not have. An existing *directory* at
one of those paths is left alone — that is somebody else's skill, not a stale
link.

To link it by hand:

```
ln -sfn "$PWD/skill/desk" ~/.claude/skills/desk
```

The skill runs `desk`, which `install.sh` puts at `~/.local/bin/desk`. That is
a generated one-line launcher, not a symlink: it bakes in `DESK_PROJECT` and
execs `scripts/desk`. A symlink could not tell where the project was, and would
resolve its own directory instead.

The skill is user-invoked only. Claude and Pi read
`disable-model-invocation: true` from the frontmatter; Codex and T3 Code read
`policy.allow_implicit_invocation: false` from `agents/openai.yaml`. The user
types `/desk` in Claude or Pi, and `$desk` in Codex or T3 Code.

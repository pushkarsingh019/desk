# Installing the `/desk` skill

`scripts/install.sh` links this directory into every agent on the machine that
has a skills directory:

```
~/.claude/skills/desk    -> <project>/skill/desk
~/.pi/agent/skills/desk  -> <project>/skill/desk
```

One canonical copy, in the repo, beside the server it drives. Editing
`SKILL.md` updates every agent at once, and the skill can never describe a
`desk` command the installed server does not have.

To link it by hand:

```
ln -sfn "$PWD/skill/desk" ~/.claude/skills/desk
```

The skill runs `desk`, which `install.sh` puts at `~/.local/bin/desk`. That is
a generated one-line launcher, not a symlink: it bakes in `DESK_PROJECT` and
execs `scripts/desk`. A symlink could not tell where the project was, and would
resolve its own directory instead.

`disable-model-invocation: true` in the frontmatter is what makes the skill
user-invoked only. The model cannot fire it on its own initiative; the user
types `/desk`. That also makes the `description` human-facing, which is why it
reads as a one-line summary rather than a list of triggers.

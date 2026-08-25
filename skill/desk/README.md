# Installing the `/desk` skill

`scripts/install.sh` copies `SKILL.md` to `~/.claude/skills/desk/SKILL.md`,
which is where Claude Code looks for user skills on studio. To install it by
hand instead:

```
mkdir -p ~/.claude/skills/desk
cp skill/desk/SKILL.md ~/.claude/skills/desk/SKILL.md
```

The skill runs `desk`, which `install.sh` puts at `~/.local/bin/desk`. That is
a generated one-line launcher, not a symlink: it bakes in `DESK_PROJECT` and
execs `scripts/desk`. A symlink could not tell where the project was, and would
resolve its own directory instead.

`disable-model-invocation: true` in the frontmatter is what makes the skill
user-invoked only. The model cannot fire it on its own initiative; the user
types `/desk`.

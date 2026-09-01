#!/bin/sh
# Remove the service, the command, and the skill links. Leaves the data
# directory alone, because that is where the sheets live.
set -e

LABEL="dev.desk"
LEGACY_LABEL="science.nairlab.desk"
BIN_DIR="${DESK_BIN_DIR:-$HOME/.local/bin}"
SKILL_DIRS="${DESK_SKILL_DIRS:-$HOME/.claude/skills/desk $HOME/.pi/agent/skills/desk $HOME/.agents/skills/desk $HOME/.codex/skills/desk}"

case "$(uname -s 2>/dev/null || echo unknown)" in
  Darwin)
    for label in "$LABEL" "$LEGACY_LABEL"; do
      launchctl bootout "gui/$(id -u)/$label" 2>/dev/null || true
      rm -f "$HOME/Library/LaunchAgents/$label.plist"
    done
    ;;
  Linux)
    if command -v systemctl >/dev/null 2>&1; then
      systemctl --user disable --now desk.service 2>/dev/null || true
    fi
    rm -f "$HOME/.config/systemd/user/desk.service"
    command -v systemctl >/dev/null 2>&1 && systemctl --user daemon-reload || true
    ;;
  *)
    echo "desk: stop the server yourself if it is running." >&2
    ;;
esac

rm -f "$BIN_DIR/desk"
for target_dir in $SKILL_DIRS; do
  # Only ever a symlink this script made; never a directory someone else owns.
  [ -L "$target_dir" ] && rm -f "$target_dir" || true
done

echo "desk: removed. Your sheets are still in ${DESK_DATA_DIR:-$HOME/.desk}."

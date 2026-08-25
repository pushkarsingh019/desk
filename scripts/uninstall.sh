#!/bin/sh
# Remove the launchd agent, the command, and the skill. Leaves ~/.desk alone,
# because that is where the sheets live.
set -e
LABEL="science.nairlab.desk"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
rm -f "$HOME/Library/LaunchAgents/$LABEL.plist"
rm -f "$HOME/.local/bin/desk"
rm -rf "$HOME/.claude/skills/desk"
echo "desk: removed. Your sheets are still in ${DESK_DATA_DIR:-$HOME/.desk}."

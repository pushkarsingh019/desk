#!/bin/sh
# Install the desk: one launchd user agent, one `desk` command, one skill.
#
# Everything here uses absolute paths on purpose. launchd runs with a minimal
# PATH that does not include ~/.local/bin, where uv lives.

set -e

PROJECT="$(cd "$(dirname "$0")/.." && pwd)"
UV="${DESK_UV:-$HOME/.local/bin/uv}"
PORT="${DESK_PORT:-7777}"
DATA_DIR="${DESK_DATA_DIR:-$HOME/.desk}"
LOG_DIR="$HOME/Library/Logs/desk"
LABEL="science.nairlab.desk"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
BIN_DIR="$HOME/.local/bin"
SKILL_DIR="$HOME/.claude/skills/desk"

if [ ! -x "$UV" ]; then
  echo "install: no uv at $UV. Install uv first, or set DESK_UV." >&2
  exit 1
fi

# macOS protects ~/Documents, ~/Desktop and ~/Downloads behind TCC. A process
# launchd starts has no way to answer the permission prompt, so opening a file
# under one of them blocks forever: the desk would bind its port, never listen,
# and log nothing. Catch that here rather than at 3am after a reboot.
case "$PROJECT/" in
  "$HOME"/Documents/*|"$HOME"/Desktop/*|"$HOME"/Downloads/*)
    if [ -z "$DESK_ALLOW_PROTECTED" ]; then
      cat >&2 <<PROTECTEDEOF
install: the project is at
    $PROJECT
which is inside a directory macOS protects (Documents, Desktop, Downloads).

A launchd agent cannot read from there — it would start, bind the port, and
then hang before ever accepting a connection, with an empty log. Move the
project somewhere launchd can reach it:

    mv "$PROJECT" ~/code/desk
    cd ~/code/desk && sh scripts/install.sh

If you have instead granted Full Disk Access to
    $PROJECT/.venv/bin/python
in System Settings > Privacy & Security, re-run with DESK_ALLOW_PROTECTED=1.
PROTECTEDEOF
      exit 1
    fi
    echo "desk: WARNING — installing from a TCC-protected directory because DESK_ALLOW_PROTECTED is set" >&2
    ;;
esac

echo "desk: project   $PROJECT"
echo "desk: uv        $UV"
echo "desk: port      $PORT"
echo "desk: data      $DATA_DIR"
echo "desk: logs      $LOG_DIR"

mkdir -p "$LOG_DIR" "$DATA_DIR" "$BIN_DIR" "$HOME/Library/LaunchAgents" "$SKILL_DIR"

# A venv carries absolute paths in the shebang of every console script, so one
# that was built somewhere else is quietly broken: the shebang points at an
# interpreter that no longer exists and the command falls through to whatever
# python happens to be on PATH — the system one, on a machine used for science.
# `uv sync` will not notice, because the package versions still match.
if [ -x "$PROJECT/.venv/bin/python" ]; then
  BUILT_FOR="$(sed -n '1s|^#!\(.*\)/\.venv/bin/python.*|\1|p' "$PROJECT/.venv/bin/desk" 2>/dev/null || true)"
  if [ -n "$BUILT_FOR" ] && [ "$BUILT_FOR" != "$PROJECT" ]; then
    echo "desk: the environment was built for $BUILT_FOR; rebuilding it here"
    rm -rf "$PROJECT/.venv"
  fi
fi

echo "desk: building the pinned environment"
"$UV" sync --project "$PROJECT" --quiet

PYTHON="$PROJECT/.venv/bin/python"
if [ ! -x "$PYTHON" ]; then
  echo "install: uv sync did not produce $PYTHON" >&2
  exit 1
fi

echo "desk: installing the desk command -> $BIN_DIR/desk"
# Generated rather than symlinked: a symlinked wrapper cannot tell where the
# project is, and would resolve its own directory instead.
cat > "$BIN_DIR/desk" <<LAUNCHEREOF
#!/bin/sh
DESK_PROJECT="$PROJECT"
export DESK_PROJECT
exec "$PROJECT/scripts/desk" "\$@"
LAUNCHEREOF
chmod +x "$BIN_DIR/desk"

echo "desk: installing the /desk skill -> $SKILL_DIR"
cp "$PROJECT/skill/desk/SKILL.md" "$SKILL_DIR/SKILL.md"

# Resolve the tailnet name once, here, while running as the user. Under
# launchd the Tailscale CLI is not reachable, so the server would otherwise
# fall back to printing the bare tailnet IP.
HOSTNAME_FOR_URL="$("$PYTHON" -c 'from desk.server import tailscale_address, tailscale_name; a = tailscale_address(); print(tailscale_name(a) if a else "")' 2>/dev/null || true)"
if [ -n "$HOSTNAME_FOR_URL" ]; then
  echo "desk: url       http://$HOSTNAME_FOR_URL:$PORT"
  HOSTNAME_ENTRY="    <key>DESK_HOSTNAME</key>
    <string>$HOSTNAME_FOR_URL</string>"
else
  echo "desk: could not resolve the tailnet name; the desk will print its tailnet IP" >&2
  HOSTNAME_ENTRY=""
fi

echo "desk: writing $PLIST"
cat > "$PLIST" <<PLISTEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$LABEL</string>

  <!-- The interpreter here is the one uv built and pinned for this project.
       launchd runs it directly rather than going through \`uv run\`: at boot the
       desk must not depend on the network, the uv cache, or a lock held by
       another uv process. \`uv sync\` in install.sh is what puts it there. -->
  <key>ProgramArguments</key>
  <array>
    <string>$PROJECT/.venv/bin/python</string>
    <string>-m</string>
    <string>desk.server</string>
  </array>

  <key>WorkingDirectory</key>
  <string>$PROJECT</string>

  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>$BIN_DIR:/usr/bin:/bin:/usr/sbin:/sbin</string>
    <key>HOME</key>
    <string>$HOME</string>
    <key>DESK_PORT</key>
    <string>$PORT</string>
    <key>DESK_DATA_DIR</key>
    <string>$DATA_DIR</string>
$HOSTNAME_ENTRY
    <key>PYTHONUNBUFFERED</key>
    <string>1</string>
  </dict>

  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>ThrottleInterval</key>
  <integer>10</integer>

  <key>StandardOutPath</key>
  <string>$LOG_DIR/desk.log</string>
  <key>StandardErrorPath</key>
  <string>$LOG_DIR/desk.log</string>
</dict>
</plist>
PLISTEOF

echo "desk: (re)loading the launchd agent"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
launchctl enable "gui/$(id -u)/$LABEL"

echo "desk: waiting for the port"
i=0
while [ $i -lt 60 ]; do
  if "$BIN_DIR/desk" status >/dev/null 2>&1; then
    break
  fi
  i=$((i + 1))
  sleep 1
done

echo
"$BIN_DIR/desk" status || {
  echo "desk: the server did not come up. Check $LOG_DIR/desk.log" >&2
  exit 1
}
echo
echo "desk: installed. Type /desk in Claude Code to present a figure."

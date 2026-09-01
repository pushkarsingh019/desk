#!/bin/sh
# Install the desk: a supervised server, one `desk` command, and one skill.
#
# Everything here uses absolute paths on purpose. A service manager runs with a
# minimal PATH that does not include ~/.local/bin, where uv usually lives.
#
# The server itself is plain Python and runs anywhere Python does. Only the
# supervision differs, so only that part branches: launchd on macOS, a systemd
# user unit on Linux, and printed instructions anywhere else.

set -e

PROJECT="$(cd "$(dirname "$0")/.." && pwd)"
OS="$(uname -s 2>/dev/null || echo unknown)"
PORT="${DESK_PORT:-7777}"
DATA_DIR="${DESK_DATA_DIR:-$HOME/.desk}"
LABEL="dev.desk"
LEGACY_LABEL="science.nairlab.desk"
BIN_DIR="${DESK_BIN_DIR:-$HOME/.local/bin}"

# Where agents keep their skills. Every directory whose *parent* exists gets a
# link; the rest are skipped, so a machine with only one agent installed gets
# only that one. Add your own with DESK_SKILL_DIRS.
SKILL_DIRS="${DESK_SKILL_DIRS:-$HOME/.claude/skills/desk $HOME/.pi/agent/skills/desk $HOME/.agents/skills/desk $HOME/.codex/skills/desk}"

# --- uv -------------------------------------------------------------------

find_uv() {
  if [ -n "$DESK_UV" ]; then
    echo "$DESK_UV"
    return
  fi
  for candidate in "$HOME/.local/bin/uv" /opt/homebrew/bin/uv /usr/local/bin/uv /usr/bin/uv; do
    if [ -x "$candidate" ]; then
      echo "$candidate"
      return
    fi
  done
  command -v uv 2>/dev/null || true
}

UV="$(find_uv)"
if [ -z "$UV" ] || [ ! -x "$UV" ]; then
  cat >&2 <<'UVEOF'
install: no uv found.

The desk pins its own Python with uv rather than borrowing the system one.
Install it, then run this script again:

    curl -LsSf https://astral.sh/uv/install.sh | sh

If uv is installed somewhere unusual, point at it: DESK_UV=/path/to/uv
UVEOF
  exit 1
fi

# --- where the project may live -------------------------------------------

# macOS protects ~/Documents, ~/Desktop and ~/Downloads behind TCC. A process
# launchd starts has no way to answer the permission prompt, so opening a file
# under one of them blocks forever: the desk would bind its port, never listen,
# and log nothing. Catch that here rather than at 3am after a reboot.
if [ "$OS" = "Darwin" ]; then
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
fi

echo "desk: project   $PROJECT"
echo "desk: platform  $OS"
echo "desk: uv        $UV"
echo "desk: port      $PORT"
echo "desk: data      $DATA_DIR"

mkdir -p "$DATA_DIR" "$BIN_DIR"

# --- the environment ------------------------------------------------------

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

# The log lives wherever the platform keeps logs, and the desk itself is the
# authority on where that is.
LOG_DIR="$("$PYTHON" -c 'from desk.cli import default_log_dir; print(default_log_dir())')"
mkdir -p "$LOG_DIR"
echo "desk: logs      $LOG_DIR"

# --- the address the desk binds -------------------------------------------

# The address is the desk's only perimeter: there is no TLS and no login. So
# the choice is made once, here, and baked into both the service and the
# command, rather than being re-guessed at every boot.
#
#   tailnet — reachable from your other machines over Tailscale
#   local   — 127.0.0.1, this machine only
#
# Set DESK_BIND to force one. Otherwise: tailnet if Tailscale is up, else local.
TAILNET_NAME="$("$PYTHON" -c 'from desk.server import tailscale_address, tailscale_name; a = tailscale_address(); print(tailscale_name(a) if a else "")' 2>/dev/null || true)"
BIND_MODE="${DESK_BIND:-}"
if [ -z "$BIND_MODE" ]; then
  if [ -n "$TAILNET_NAME" ]; then BIND_MODE=tailnet; else BIND_MODE=local; fi
fi

if [ "$BIND_MODE" = "tailnet" ] && [ -z "$TAILNET_NAME" ]; then
  echo "desk: DESK_BIND=tailnet but Tailscale is not up; the desk will wait for it at start" >&2
fi

if [ "$BIND_MODE" = "tailnet" ]; then
  URL_HOST="$TAILNET_NAME"
  echo "desk: bind      tailnet — reachable from your tailnet"
else
  URL_HOST="localhost"
  echo "desk: bind      local — this machine only"
  if [ -z "$TAILNET_NAME" ]; then
    echo "desk:           (install Tailscale and re-run to browse it from another machine)"
  fi
fi
echo "desk: url       http://$URL_HOST:$PORT"

# --- the `desk` command ---------------------------------------------------

echo "desk: installing the desk command -> $BIN_DIR/desk"
# Generated rather than symlinked: a symlinked wrapper cannot tell where the
# project is, and would resolve its own directory instead. It also carries the
# bind decision, so the command looks for the desk exactly where the service
# put it.
cat > "$BIN_DIR/desk" <<LAUNCHEREOF
#!/bin/sh
DESK_PROJECT="$PROJECT"
DESK_BIND="\${DESK_BIND:-$BIND_MODE}"
export DESK_PROJECT DESK_BIND
exec "$PROJECT/scripts/desk" "\$@"
LAUNCHEREOF
chmod +x "$BIN_DIR/desk"

case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *) echo "desk: NOTE — $BIN_DIR is not on your PATH. Add it, or call $BIN_DIR/desk." >&2 ;;
esac

# --- the skill ------------------------------------------------------------

# The skill ships with the server it drives, so the repo holds the one copy and
# every agent that can run it points at that. Editing the repo updates them all,
# and a skill can never describe a `desk` command the installed server does not
# have. Each tool that is present gets a link; absent ones are skipped.
LINKED=0
for target_dir in $SKILL_DIRS; do
  [ -d "$(dirname "$target_dir")" ] || continue
  # A real directory there is somebody else's skill, not a stale link of ours.
  # Say so and leave it: silently deleting a skill a user wrote is unforgivable.
  if [ -e "$target_dir" ] && [ ! -L "$target_dir" ]; then
    echo "desk: NOTE — $target_dir already exists and is not a link; leaving it alone." >&2
    continue
  fi
  ln -sfn "$PROJECT/skill/desk" "$target_dir"
  echo "desk: skill     -> $target_dir"
  LINKED=$((LINKED + 1))
done
if [ "$LINKED" -eq 0 ]; then
  echo "desk: no agent skills directory found. Link it by hand once you have one:" >&2
  echo "desk:   ln -sfn $PROJECT/skill/desk ~/.claude/skills/desk" >&2
fi

# --- supervision ----------------------------------------------------------

install_launchd() {
  PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
  mkdir -p "$HOME/Library/LaunchAgents"

  if [ -n "$TAILNET_NAME" ]; then
    HOSTNAME_ENTRY="    <key>DESK_HOSTNAME</key>
    <string>$TAILNET_NAME</string>"
  else
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
    <key>DESK_BIND</key>
    <string>$BIND_MODE</string>
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
  # An install from before the agent was renamed would otherwise keep running
  # and hold the port.
  launchctl bootout "gui/$(id -u)/$LEGACY_LABEL" 2>/dev/null || true
  rm -f "$HOME/Library/LaunchAgents/$LEGACY_LABEL.plist"
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  launchctl bootstrap "gui/$(id -u)" "$PLIST"
  launchctl enable "gui/$(id -u)/$LABEL"
}

install_systemd() {
  UNIT_DIR="$HOME/.config/systemd/user"
  UNIT="$UNIT_DIR/desk.service"
  mkdir -p "$UNIT_DIR"

  echo "desk: writing $UNIT"
  cat > "$UNIT" <<UNITEOF
[Unit]
Description=Desk — a visuo-spatial sketchpad for agent-produced figures
After=network.target

[Service]
# The interpreter uv built and pinned for this project, run directly rather
# than through \`uv run\`: at boot the desk must not depend on the network, the
# uv cache, or a lock held by another uv process.
ExecStart=$PROJECT/.venv/bin/python -m desk.server
WorkingDirectory=$PROJECT
Environment=DESK_PORT=$PORT
Environment=DESK_DATA_DIR=$DATA_DIR
Environment=DESK_BIND=$BIND_MODE
Environment=PYTHONUNBUFFERED=1
Restart=always
RestartSec=10
StandardOutput=append:$LOG_DIR/desk.log
StandardError=append:$LOG_DIR/desk.log

[Install]
WantedBy=default.target
UNITEOF

  echo "desk: (re)loading the systemd user unit"
  systemctl --user daemon-reload
  systemctl --user enable desk.service >/dev/null 2>&1 || true
  systemctl --user restart desk.service

  # Without lingering, the unit stops when the last session ends — so a desk
  # on a headless box would die the moment you log out of SSH.
  if command -v loginctl >/dev/null 2>&1; then
    if [ "$(loginctl show-user "$(id -un)" -p Linger --value 2>/dev/null)" != "yes" ]; then
      echo "desk: NOTE — the desk stops when you log out. To keep it running:"
      echo "desk:   sudo loginctl enable-linger $(id -un)"
    fi
  fi
}

manual_instructions() {
  cat <<MANUALEOF

desk: $OS has no supervisor this script knows how to write, so the desk is
      installed but not started automatically. Run it yourself:

          $PYTHON -m desk.server

      Or let the command start it on demand — \`desk present\` brings the
      server up if the port is closed, which covers most days.

      To have it start at login, wrap that command in whatever your system
      uses, with these in the environment:

          DESK_PORT=$PORT
          DESK_DATA_DIR=$DATA_DIR
          DESK_BIND=$BIND_MODE
MANUALEOF
}

SUPERVISED=yes
case "$OS" in
  Darwin) install_launchd ;;
  Linux)
    if command -v systemctl >/dev/null 2>&1; then
      install_systemd
    else
      SUPERVISED=no
      manual_instructions
    fi
    ;;
  *)
    SUPERVISED=no
    manual_instructions
    ;;
esac

# --- did it come up -------------------------------------------------------

if [ "$SUPERVISED" = "no" ]; then
  echo
  echo "desk: installed. Start the server, then type /desk in your agent."
  exit 0
fi

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
echo "desk: installed. Type /desk in your agent to present a figure."

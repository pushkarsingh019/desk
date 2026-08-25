"""`desk` — the command the /desk skill runs.

`desk present` resolves a figure, starts the server if the port is closed,
publishes, and prints the desk URL. It exits nonzero and says why on any
failure, so a present that did not happen is never reported as one that did.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from desk.server import DEFAULT_PORT, resolve_host
from desk.store import ALLOWED_EXTENSIONS, check_publishable
from desk.store import PublishError

#: How recently a file must have been touched to count as "produced in this
#: session" when `/desk` is called with no argument.
RECENT_SECONDS = 6 * 60 * 60

SKIP_DIRS = {
    ".git", ".venv", "venv", "node_modules", "__pycache__", ".desk",
    ".mypy_cache", ".pytest_cache", "site-packages", ".tox", "dist", "build",
}


def port_open(host: str, port: int, timeout: float = 0.4) -> bool:
    try:
        with socket.create_connection((host, port), timeout):
            return True
    except OSError:
        return False


def desk_base(host: str, port: int) -> str:
    return f"http://{host}:{port}"


def search_root(explicit: str | None = None) -> Path:
    """Where bare `/desk` looks for a figure.

    Not simply the process's cwd: the skill runs this through `uv run
    --directory`, which lands the process in the desk's own repo. The wrapper
    passes the directory the user was actually standing in.
    """
    if explicit:
        return Path(explicit).expanduser().resolve()
    from_env = os.environ.get("DESK_CWD")
    if from_env:
        return Path(from_env).expanduser().resolve()
    return Path.cwd()


def find_latest_figure(root: Path) -> Path | None:
    """The most recently modified allowlisted figure under `root`.

    This is what bare `/desk` presents. It is deliberately dumb: newest wins.
    Dotfiles, `*_tmp*`, and the desk's own store are never candidates.
    """
    newest: tuple[float, Path] | None = None
    cutoff = time.time() - RECENT_SECONDS
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        for name in filenames:
            if Path(name).suffix.lower() not in ALLOWED_EXTENSIONS:
                continue
            path = Path(dirpath) / name
            try:
                check_publishable(path)
                mtime = path.stat().st_mtime
            except (PublishError, OSError):
                continue
            if mtime < cutoff:
                continue
            if newest is None or mtime > newest[0]:
                newest = (mtime, path)
    return newest[1] if newest else None


def start_server(host: str, port: int, timeout: float = 45.0) -> None:
    """Bring the desk up in the background and wait for its port."""
    log_dir = Path(os.environ.get("DESK_LOG_DIR") or Path.home() / "Library" / "Logs" / "desk")
    log_dir.mkdir(parents=True, exist_ok=True)
    log = open(log_dir / "desk.log", "ab")
    subprocess.Popen(
        [sys.executable, "-m", "desk.server"],
        stdout=log,
        stderr=log,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
        cwd=str(Path.home()),
    )
    deadline = time.time() + timeout
    while time.time() < deadline:
        if port_open(host, port):
            return
        time.sleep(0.2)
    raise SystemExit(
        f"desk: started the server but {host}:{port} never opened. "
        f"See {log_dir / 'desk.log'}."
    )


def publish(base: str, source_path: Path) -> dict:
    request = urllib.request.Request(
        base + "/api/publish",
        data=json.dumps({"source_path": str(source_path)}).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        try:
            detail = json.loads(detail)["error"]
        except Exception:
            pass
        raise SystemExit(f"desk: the desk refused {source_path}: {detail}")
    except urllib.error.URLError as exc:
        raise SystemExit(f"desk: could not reach the desk at {base}: {exc.reason}")


def cmd_present(args) -> int:
    host, hostname, port = where()

    if args.path:
        source = Path(args.path).expanduser()
        if not source.is_absolute():
            source = search_root(args.directory) / source
        if not source.exists():
            raise SystemExit(f"desk: no such file: {source}")
    else:
        root = search_root(args.directory)
        source = find_latest_figure(root)
        if source is None:
            raise SystemExit(
                "desk: found no figure to present. Looked for "
                f"{' '.join(sorted(ALLOWED_EXTENSIONS))} files modified in the last "
                f"{RECENT_SECONDS // 3600}h under {root}. "
                "Pass a path: /desk path/to/figure.svg"
            )

    source = Path(os.path.realpath(source))
    try:
        check_publishable(source)
    except PublishError as exc:
        raise SystemExit(f"desk: {exc}")

    base = desk_base(host, port)
    if not port_open(host, port):
        print(f"desk: starting the server on {host}:{port}", file=sys.stderr)
        start_server(host, port)

    result = publish(base, source)
    sheet = result["sheet"]
    where_it_went = "updated in place" if sheet["version"] > 1 else "waiting in the inbox"
    print(f"{sheet['name']} v{sheet['version']} — {where_it_went}")
    print(f"{desk_base(hostname, port)}")
    return 0


def cmd_status(args) -> int:
    host, hostname, port = where()
    if not port_open(host, port):
        print(f"desk: not running on {host}:{port}")
        return 1
    with urllib.request.urlopen(desk_base(host, port) + "/api/state", timeout=10) as response:
        state = json.load(response)
    placed = sum(1 for p in state["layout"]["sheets"].values() if not p["inbox"])
    inbox = len(state["layout"]["sheets"]) - placed
    print(f"{desk_base(hostname, port)}")
    print(f"{len(state['sheets'])} sheets — {placed} on the desk, {inbox} in the inbox, "
          f"{len(state['trash'])} in the trash")
    print(f"data: {state.get('data_dir', '?')}")
    return 0


def where() -> tuple[str, str, int]:
    port = int(os.environ.get("DESK_PORT") or DEFAULT_PORT)
    try:
        host, hostname = resolve_host(wait=0)
    except Exception as exc:
        raise SystemExit(f"desk: {exc}")
    return host, hostname, port


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="desk", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command")

    present = sub.add_parser("present", help="put a figure on the desk")
    present.add_argument("path", nargs="?", help="the figure to present (default: the newest one)")
    present.add_argument(
        "--in",
        dest="directory",
        default=None,
        help="the directory to resolve from (default: $DESK_CWD, then the cwd)",
    )
    present.set_defaults(func=cmd_present)

    sub.add_parser("status", help="report where the desk is and what is on it").set_defaults(func=cmd_status)

    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 2
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

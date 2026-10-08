"""`desk` — the command the /desk skill runs.

`desk present` resolves a figure, starts the server if the port is closed,
publishes, and prints the desk URL. With `--to` it sends the figure to
someone else's desk instead. It exits nonzero and says why on any failure, so
a present that did not happen is never reported as one that did.

`desk feedback` reads the user's open comments back, with where each one is
in terms an agent can act on. It never starts the server: feedback is read,
not made, and a desk that is not running has none.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from desk.server import DEFAULT_PORT, resolve_host, tailscale_name
from desk.store import ALLOWED_EXTENSIONS, FRAME_KINDS, check_origin, check_publishable
from desk.store import PublishError, canonical_source_path

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


def default_log_dir() -> Path:
    """Where the desk writes its log, by the convention of the platform.

    Each platform has one place a user already knows to look, and the install
    script asks this function rather than guessing, so the log the desk writes
    and the log the installer prints are the same file on every OS.
    """
    override = os.environ.get("DESK_LOG_DIR")
    if override:
        return Path(override).expanduser()
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Logs" / "desk"
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "desk" / "logs"
    return Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state") / "desk"


def start_server(host: str, port: int, timeout: float = 45.0) -> None:
    """Bring the desk up in the background and wait for its port."""
    log_dir = default_log_dir()
    log_dir.mkdir(parents=True, exist_ok=True)
    log = open(log_dir / "desk.log", "ab")
    # Detach, so the desk outlives the shell that presented the first figure.
    # The two platforms spell that differently and each rejects the other's
    # spelling outright.
    detach = (
        {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS}
        if os.name == "nt"
        else {"start_new_session": True}
    )
    subprocess.Popen(
        [sys.executable, "-m", "desk.server"],
        stdout=log,
        stderr=log,
        stdin=subprocess.DEVNULL,
        cwd=str(Path.home()),
        **detach,
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
    """Publish a file that is on the desk's own machine, by path."""
    return _post_publish(base, {"source_path": str(source_path)}, source_path.name, "the desk")


def send(base: str, source_path: Path, origin: str, whose: str) -> dict:
    """Send a file's bytes to a desk on another machine.

    The path travels too, spelled with forward slashes whatever this machine
    uses, because it is the sheet's identity over there: the same file sent
    again from here lands on the same sheet.
    """
    payload = {
        "origin": origin,
        "source_path": source_path.as_posix(),
        "content": base64.b64encode(source_path.read_bytes()).decode("ascii"),
    }
    return _post_publish(base, payload, source_path.name, f"{whose}'s desk", timeout=60)


def _post_publish(base: str, payload: dict, name: str, whose: str, timeout: float = 20) -> dict:
    request = urllib.request.Request(
        base + "/api/publish",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        try:
            detail = json.loads(detail)["error"]
        except Exception:
            pass
        raise SystemExit(f"desk: {whose} refused {name}: {detail}")
    except urllib.error.URLError as exc:
        raise SystemExit(f"desk: could not reach {whose} at {base}: {exc.reason}")
    except (TimeoutError, OSError) as exc:
        raise SystemExit(f"desk: could not reach {whose} at {base}: {exc}")


def this_machine() -> str:
    """The name this machine sends under: its tailnet machine name, else its
    hostname. The recipient's sheet is identified by it, so it has to come out
    the same on every send from here — which it does as long as the machine
    keeps its name."""
    name = tailscale_name("") or socket.gethostname()
    return name.split(".")[0] or "unknown"


def remote_desk(to: str) -> tuple[str, str]:
    """Return (base URL, the name to call it by) for a `--to` argument.

    `bob-mac` is a machine on the tailnet on the default port; `bob-mac:7788`
    names a port; a full `http://` URL is taken as is.
    """
    to = to.strip()
    if to.startswith(("http://", "https://")):
        base = to.rstrip("/")
        host = base.split("//", 1)[1].split("/", 1)[0]
        return base, host.rsplit(":", 1)[0] or host
    host, _, port = to.rpartition(":")
    if not host:
        host, port = to, ""
    if not host or (port and not port.isdigit()):
        raise SystemExit(f"desk: --to needs a machine name, or machine:port, not {to!r}")
    return f"http://{host}:{port or DEFAULT_PORT}", host


def cmd_present(args) -> int:
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

    if args.to:
        # Someone else's desk. Nothing here is started, and nothing lands
        # here: their desk is reachable or the send fails, loudly.
        base, whose = remote_desk(args.to)
        try:
            origin = check_origin(this_machine())
        except PublishError as exc:
            # Their desk would refuse it anyway; say why here, where the
            # name comes from, rather than quoting a refusal from over there.
            raise SystemExit(f"desk: cannot send under this machine's name: {exc}")
        result = send(base, source, origin, whose)
        sheet = result["sheet"]
        where_it_went = (
            f"updated in place on {whose}'s desk"
            if sheet["version"] > 1
            else f"waiting in {whose}'s inbox"
        )
        print(f"{sheet['name']} v{sheet['version']} — {where_it_went}")
        print(result.get("desk_url") or base)
        return 0

    host, hostname, port = where()
    base = desk_base(host, port)
    if not port_open(host, port):
        print(f"desk: starting the server on {host}:{port}", file=sys.stderr)
        start_server(host, port)

    result = publish(base, source)
    sheet = result["sheet"]
    where_it_went = "updated in place" if sheet["version"] > 1 else "waiting in the inbox"
    print(f"{sheet['name']} v{sheet['version']} — {where_it_went}")
    print(f"{desk_base(hostname, port)}")
    # The third line, only when there is something to read. A sent sheet
    # never gets one: its comments stay on the desk they were made on.
    open_comments = sheet.get("open_comments") or 0
    if open_comments:
        print(f"{_count(open_comments, 'open comment')} on this sheet — run: desk feedback {source}")
    return 0


# --- feedback ---------------------------------------------------------------


def cmd_feedback(args) -> int:
    """Print every open comment on the current desk, or on one sheet.

    Nothing here measures anything: the natural size and the comment numbers
    come from the desk, and this command only turns fractions into pixels and
    a grid cell. It never starts the server — unlike `present`, there is
    nothing to do on a desk that is not running except say so.
    """
    host, hostname, port = where()
    base = desk_base(host, port)
    if not port_open(host, port):
        raise SystemExit(f"desk: not running on {host}:{port}, so there is no feedback to read")
    try:
        with urllib.request.urlopen(base + "/api/state", timeout=10) as response:
            state = json.load(response)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise SystemExit(f"desk: could not reach the desk at {base}: {exc}")

    # Live sheets only: a trashed sheet's comments are in the trash with it.
    sheets = state["sheets"]
    if args.path:
        wanted = feedback_path(args.path, args.directory)
        # A local sheet first; a sent sheet with the same path there second.
        matches = sorted(
            (s for s in sheets if s["source_path"] == wanted),
            key=lambda s: s.get("origin") is not None,
        )
        if not matches:
            raise SystemExit(f"desk: {wanted} is not a sheet on the desk {state['desk']!r}")
        sheets = matches[:1]

    report = [feedback_for(sheet) for sheet in sheets]
    report = [r for r in report if r["comments"]]
    if args.json:
        print(json.dumps({"desk": state["desk"], "sheets": report}, indent=2))
        return 0
    if not report:
        print("no open feedback")
        return 0
    print("\n\n".join(format_feedback(r) for r in report))
    return 0


def feedback_path(path: str, directory: str | None) -> str:
    """Spell a path the way the desk spells a sheet's source path: the way
    `present` resolves one, without requiring the file to still be there —
    a sent sheet's path names a file on another machine."""
    source = Path(path).expanduser()
    if not source.is_absolute():
        source = search_root(directory) / source
    return str(canonical_source_path(Path(os.path.realpath(source))))


def feedback_for(sheet: dict) -> dict:
    """One sheet's open comments, each with where it is in every useful term."""
    comments = [c for c in sheet.get("comments", []) if c.get("resolved_at") is None]
    return {
        "source_path": sheet["source_path"],
        "name": sheet["name"],
        "origin": sheet.get("origin"),
        "kind": sheet["kind"],
        "version": sheet["version"],
        "natural_size": sheet.get("natural_size"),
        "comments": [comment_for(sheet, c) for c in comments],
    }


def comment_for(sheet: dict, comment: dict) -> dict:
    retained = comment["version"] in sheet["versions"]
    on_commented = (
        pixels_of(comment["anchor"], comment.get("natural_size"))
        if retained and comment["version"] != sheet["version"]
        else None
    )
    return {
        "id": comment["id"],
        "number": comment["number"],
        "version": comment["version"],
        "retained": retained,
        "anchor": comment["anchor"],
        "pixels": pixels_of(comment["anchor"], sheet.get("natural_size")),
        "pixels_on_version": on_commented,
        "location": location_of(comment["anchor"]),
        "text": comment["text"],
    }


def pixels_of(anchor, size) -> dict | None:
    """The anchor against a natural box, in whole pixels; None without one."""
    if anchor is None or not size:
        return None
    return {
        "x": round(anchor["x"] * size["w"]),
        "y": round(anchor["y"] * size["h"]),
        "w": round(anchor["w"] * size["w"]),
        "h": round(anchor["h"] * size["h"]),
    }


def location_of(anchor) -> str:
    """A coarse verbal location from a three-by-three grid of the box."""
    if anchor is None or (anchor["w"] >= 0.9 and anchor["h"] >= 0.9):
        return "whole sheet"
    cx = anchor["x"] + anchor["w"] / 2
    cy = anchor["y"] + anchor["h"] / 2
    col = 0 if cx < 1 / 3 else 1 if cx < 2 / 3 else 2
    row = 0 if cy < 1 / 3 else 1 if cy < 2 / 3 else 2
    if row == 1 and col == 1:
        return "centre"
    if row == 1:
        return ("left", "", "right")[col] + " edge, middle"
    if col == 1:
        return ("top", "", "bottom")[row] + " edge, middle"
    return ("upper", "", "lower")[row] + " " + ("left", "", "right")[col]


def format_feedback(report: dict) -> str:
    size = report["natural_size"]
    lines = [report["source_path"]]
    if report["origin"]:
        lines.append(
            f"  from {report['origin']} — a copy; the script that made it is not on this machine"
        )
    about = f"  v{report['version']} · {_count(len(report['comments']), 'open comment')}"
    about += f" · {_size(size)} px" if size else " · natural size not available"
    lines.append(about)
    for c in report["comments"]:
        made_on = f"on v{c['version']}" + ("" if c["retained"] else ", no longer retained")
        lines.append(f"  #{c['number']}  {made_on} — {c['location']}")
        a = c["anchor"]
        if a is not None:
            lines.append(f"      fractions: {_span(a, 1, 1, '{:.3f}', note=True)}")
            if c["pixels"]:
                lines.append(
                    f"      pixels on v{report['version']}: {_span(a, size['w'], size['h'], '{:d}')}"
                    f" (of {_size(size)})"
                )
            else:
                lines.append(f"      pixels: not available — {_no_size(report['kind'])}")
            if c["pixels_on_version"]:
                p = c["pixels_on_version"]
                on = {"x": p["x"], "y": p["y"], "w": p["w"], "h": p["h"]}
                lines.append(f"      pixels on v{c['version']}: {_pixel_span(on)}")
        for line in c["text"].splitlines() or [""]:
            lines.append(f"      {line}")
    return "\n".join(lines)


def _span(a: dict, sw, sh, fmt: str, note: bool = False) -> str:
    """`x 0.62–0.90, y 0.08–0.22` for a rectangle; `x 0.50, y 0.25` for a point."""
    x0, y0 = a["x"] * sw, a["y"] * sh
    x1, y1 = (a["x"] + a["w"]) * sw, (a["y"] + a["h"]) * sh
    if fmt == "{:d}":
        x0, y0, x1, y1 = (round(v) for v in (x0, y0, x1, y1))
    if a["w"] == 0 and a["h"] == 0:
        return f"x {fmt.format(x0)}, y {fmt.format(y0)}" + (" (a point)" if note else "")
    return f"x {fmt.format(x0)}–{fmt.format(x1)}, y {fmt.format(y0)}–{fmt.format(y1)}"


def _pixel_span(p: dict) -> str:
    if p["w"] == 0 and p["h"] == 0:
        return f"x {p['x']}, y {p['y']}"
    return f"x {p['x']}–{p['x'] + p['w']}, y {p['y']}–{p['y'] + p['h']}"


def _size(size: dict) -> str:
    return f"{size['w']}×{size['h']}"


def _no_size(kind: str) -> str:
    if kind in FRAME_KINDS:
        return f"a .{kind} sheet is shown in a frame and has no natural size; its comments are on the whole sheet"
    return f"the desk could not read a width and height from this .{kind}"


def _count(n: int, noun: str) -> str:
    return f"{n} {noun}" + ("" if n == 1 else "s")


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
    others = [d for d in state.get("desks", []) if d != state.get("desk")]
    print(f"desk: {state.get('desk', 'main')}" + (f"  (also: {', '.join(others)})" if others else ""))
    print(f"{len(state['sheets'])} sheets — {placed} on the desk, {inbox} in the inbox, "
          f"{len(state['trash'])} in the trash")
    print(f"data: {state.get('data_dir', '?')}")
    print(f"log:  {default_log_dir() / 'desk.log'}")
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
    present.add_argument(
        "--to",
        dest="to",
        default=None,
        metavar="MACHINE[:PORT]",
        help="put it on that machine's desk instead of this one (a tailnet name, or a URL)",
    )
    present.set_defaults(func=cmd_present)

    feedback = sub.add_parser("feedback", help="read the user's open comments back")
    feedback.add_argument("path", nargs="?", help="one sheet's figure (default: every sheet with open comments)")
    feedback.add_argument(
        "--in",
        dest="directory",
        default=None,
        help="the directory to resolve a relative path from (default: $DESK_CWD, then the cwd)",
    )
    feedback.add_argument("--json", action="store_true", help="print the report as one JSON object")
    feedback.set_defaults(func=cmd_feedback)

    sub.add_parser("status", help="report where the desk is and what is on it").set_defaults(func=cmd_status)

    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 2
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

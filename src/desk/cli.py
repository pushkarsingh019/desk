"""`desk` — the command the /desk skill runs.

`desk present` resolves a figure, starts the server if the port is closed,
publishes, and prints the desk URL. With `--to` it sends the figure to
someone else's desk instead. It exits nonzero and says why on any failure, so
a present that did not happen is never reported as one that did.

`desk feedback` reads the user's open comments back, with where each one is
in terms an agent can act on. It never starts the server: feedback is read,
not made, and a desk that is not running has none.

`desk export` writes a sheet's comments, open and resolved, and its retained
versions to a directory another agent can open without the desk: the record
of what the user said about a figure and what it looked like before and
after. A read, like `feedback`.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from desk.server import DEFAULT_PORT, resolve_host, tailscale_name
from desk.store import ALLOWED_EXTENSIONS, FRAME_KINDS, check_origin, check_publishable
from desk.store import PublishError, canonical_source_path, is_under

#: How recently a file must have been touched to count as "produced in this
#: session" when `/desk` is called with no argument.
RECENT_SECONDS = 6 * 60 * 60
#: How long `desk feedback --wait` holds by default. Under the longest a
#: harness lets a background command run (Claude Code: two hours), so the
#: agent hears "nobody asked" from the desk rather than a kill from the harness.
DEFAULT_WAIT_MINUTES = 110.0

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
    """Print the open comments on what the agent just put up, or on one sheet.

    A desk holds many sheets at once, and the ones the user is commenting on
    are the ones this agent just made. So with no path the scope is the same
    as bare `present`: the directory the agent is working in. Every live
    sheet under it with open comments is reported, newest first. `--all` is
    the whole desk.

    Nothing here measures anything: the natural size and the comment numbers
    come from the desk, and this command only turns fractions into pixels and
    a grid cell. It never starts the server — unlike `present`, there is
    nothing to do on a desk that is not running except say so.

    `--wait` is the same report, later: it holds until the user presses the
    feedback button on a sheet in scope, then prints that sheet and exits.
    Waiting reads and nothing else — it never writes, resolves, or removes a
    comment, and nothing reaches the agent until the user asks.
    """
    base, state = running_desk("so there is no feedback to read")
    sheets, scope, wanted = sheets_in_scope(state, args.path, args.all, args.directory)

    if args.wait is not None:
        if args.path:
            wait_scope = {"path": wanted}
        elif scope is not None:
            wait_scope = {"under": str(scope)}
        else:
            wait_scope = {}
        sheet = wait_for_feedback(base, wait_scope, args.wait * 60)
        if sheet is None:
            print(f"waited {_minutes(args.wait)} and nobody asked for feedback")
            return 0
        sheets = [sheet]

    report = [feedback_for(sheet) for sheet in sheets]
    report = [r for r in report if r["comments"]]
    if args.json:
        payload = {"desk": state["desk"], "sheets": report}
        if scope is not None:
            payload["under"] = str(scope)
        text = json.dumps(payload, indent=2)
    elif not report:
        if scope is not None:
            text = f"no open feedback under {scope} (desk feedback --all for the whole desk)"
        else:
            text = "no open feedback"
    else:
        text = "\n\n".join(format_feedback(r) for r in report)
    if args.then and report:
        # A harness with no way to hear a background command finish gets the
        # report handed to a command of its own: on stdin, with the sheet's
        # path in the environment. The command is the harness's; this is not.
        env = {**os.environ, "DESK_SHEET": report[0]["source_path"], "DESK_DESK": state["desk"]}
        done = subprocess.run(args.then, shell=True, input=text + "\n", text=True, env=env)
        return done.returncode
    print(text)
    return 0


def running_desk(or_else: str) -> tuple[str, dict]:
    """The desk's base URL and its state, for a command that only reads.

    Never starts the server: unlike `present`, there is nothing to do on a
    desk that is not running except say so."""
    host, hostname, port = where()
    base = desk_base(host, port)
    if not port_open(host, port):
        raise SystemExit(f"desk: not running on {host}:{port}, {or_else}")
    try:
        with urllib.request.urlopen(base + "/api/state", timeout=10) as response:
            state = json.load(response)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise SystemExit(f"desk: could not reach the desk at {base}: {exc}")
    return base, state


def sheets_in_scope(state: dict, path, everything: bool, directory) -> tuple[list, Path | None, str | None]:
    """The sheets a read-only command is about, newest first.

    A path is one sheet. Otherwise the scope is the directory the agent is
    working in — a desk holds many sheets at once, and the ones the user is
    commenting on are the ones this agent just made — or, with `--all`, the
    whole current desk. Live sheets only: a trashed sheet's comments are in
    the trash with it. Returns the sheets, the directory when that is the
    scope, and the path when one was given."""
    sheets = state["sheets"]
    scope = None
    wanted = None
    if path:
        wanted = feedback_path(path, directory)
        # A local sheet first; a sent sheet with the same path there second.
        matches = sorted(
            (s for s in sheets if s["source_path"] == wanted),
            key=lambda s: s.get("origin") is not None,
        )
        if not matches:
            raise SystemExit(f"desk: {wanted} is not a sheet on the desk {state['desk']!r}")
        sheets = matches[:1]
    elif not everything:
        scope = search_root(directory)
        # A sent sheet's path names a file on another machine, so it is never
        # under this directory, whatever its spelling.
        sheets = [
            s for s in sheets
            if s.get("origin") is None and is_under(s["source_path"], scope)
        ]
    sheets = sorted(sheets, key=lambda s: s.get("updated_at", 0), reverse=True)
    return sheets, scope, wanted


#: The manifest format. Bump when a reader of an old bundle would misread a new one.
EXPORT_SCHEMA = 1


def cmd_export(args) -> int:
    """Write the comments and retained versions of the sheets in scope to a
    directory another agent can open without the desk.

    Every comment, open and resolved — resolved ones are the interesting
    half of a record of critique — with the version it was made on and the
    version it was resolved on, and the retained version files byte for
    byte. The manifest never claims a file it does not have: a version a
    comment names that has been evicted is listed with `retained: false`.

    A read, like `feedback`: never starts the server, never writes to the desk.
    """
    base, state = running_desk("so there is nothing to export")
    sheets, scope, _ = sheets_in_scope(state, args.path, args.all, args.directory)
    sheets = [s for s in sheets if s.get("comments")]
    if not sheets:
        if scope is not None:
            print(f"nothing to export under {scope} (desk export --all for the whole desk)")
        else:
            print("nothing to export")
        return 0

    out = Path(args.out).expanduser() if args.out else None
    if out is None:
        out = search_root(args.directory) / time.strftime("desk-export-%Y%m%d-%H%M%S")
    elif not out.is_absolute():
        out = search_root(args.directory) / out
    if out.exists() and (not out.is_dir() or any(out.iterdir())):
        raise SystemExit(f"desk: {out} exists and is not empty; export into a new or empty directory")
    out.mkdir(parents=True, exist_ok=True)

    manifest = {
        "schema": EXPORT_SCHEMA,
        "desk": state["desk"],
        "exported_at": time.time(),
        "sheets": [],
    }
    if scope is not None:
        manifest["under"] = str(scope)
    files = 0
    for index, sheet in enumerate(sheets, start=1):
        stem = Path(sheet["name"]).stem or "sheet"
        rel_dir = Path("sheets") / f"{index:02d}-{stem}"
        (out / rel_dir).mkdir(parents=True, exist_ok=True)
        retained = {v["n"]: v for v in sheet.get("history", [])}
        named = {c["version"] for c in sheet["comments"]}
        named |= {c["resolved_version"] for c in sheet["comments"] if c.get("resolved_version")}
        versions = []
        for n in sorted(set(retained) | named):
            record = retained.get(n)
            if record is None:
                versions.append(
                    {"n": n, "sha256": None, "bytes": None, "created_at": None, "retained": False, "file": None}
                )
                continue
            rel_file = rel_dir / f"v{n}.{sheet['kind']}"
            fetch(base + record["content_url"], out / rel_file)
            files += 1
            versions.append(
                {
                    "n": n,
                    "sha256": record.get("sha256"),
                    "bytes": record.get("bytes"),
                    "created_at": record.get("created_at"),
                    "retained": True,
                    "file": rel_file.as_posix(),
                }
            )
        manifest["sheets"].append(
            {
                "source_path": sheet["source_path"],
                "origin": sheet.get("origin"),
                "kind": sheet["kind"],
                "name": sheet["name"],
                "dir": rel_dir.as_posix(),
                "natural_size": sheet.get("natural_size"),
                "versions": versions,
                "comments": [
                    {
                        "id": c["id"],
                        "number": c["number"],
                        "version": c["version"],
                        "resolved_version": c.get("resolved_version"),
                        "anchor": c["anchor"],
                        "location": location_of(c["anchor"]),
                        "pixels_on_version": pixels_of(c["anchor"], c.get("natural_size")),
                        "text": c["text"],
                        "created_at": c["created_at"],
                        "resolved_at": c["resolved_at"],
                    }
                    for c in sheet["comments"]
                ],
            }
        )
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    (out / "README.md").write_text(EXPORT_README)
    comments = sum(len(s["comments"]) for s in manifest["sheets"])
    print(
        f"{out}\n{_count(len(manifest['sheets']), 'sheet')}, {_count(comments, 'comment')}, "
        f"{_count(files, 'version file')}"
    )
    return 0


def fetch(url: str, dest: Path) -> None:
    """The store's copy of one version, untouched, to a file."""
    try:
        with urllib.request.urlopen(url, timeout=30) as response, open(dest, "wb") as sink:
            shutil.copyfileobj(response, sink)
    except (urllib.error.URLError, OSError) as exc:
        raise SystemExit(f"desk: could not fetch {url}: {exc}")


EXPORT_README = """\
# A desk export

What one person said about some figures, and what the figures looked like
when they said it. Written by `desk export`; nothing here needs the desk to
read.

`manifest.json` lists the sheets. A **sheet** is one figure, identified by
its `source_path` on the machine it was made on (`origin` names that
machine when it was not the exporting one). Each sheet has:

- `versions` — every revision this export knows about, oldest first.
  `file` is the path of its bytes relative to this directory, exactly as the
  figure was published; `retained: false` with `file: null` means the desk
  no longer has that revision and only a comment still names it.
- `comments` — every comment the user pinned on the sheet, in the order made,
  open and resolved alike. `version` is the revision it was made on;
  `resolved_version` is the revision on screen when the user resolved it,
  or null while it is open (or when it was resolved before the desk recorded
  this). `anchor` is where, as a rectangle in fractions `0..1` of the
  figure's natural box — `w` and `h` of 0 is a point, null is the whole
  sheet — and `pixels_on_version` is the same against the pixel size of
  the version it was made on, when that could be read. `location` is a
  coarse word for the spot. `text` is what the user said.

Only the user writes a comment, and only the user resolves one. A resolved
comment on version *a* with `resolved_version` *b* reads: the user pointed
at this spot on v*a* and said this; by v*b* it was met. The agent's
side — what was changed in the plotting code between the two — was never on
the desk and is not here.
"""


def wait_for_feedback(base: str, scope: dict, seconds: float) -> dict | None:
    """Hold until the user asks for feedback on a sheet in `scope`, for at
    most `seconds`. The desk answers each poll within a minute, so this asks
    again until the deadline; a desk that stops mid-wait ends it, loudly."""
    deadline = time.monotonic() + seconds
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        query = urllib.parse.urlencode({**scope, "timeout": f"{min(remaining, 60):.3f}"})
        try:
            with urllib.request.urlopen(base + "/api/feedback/wait?" + query, timeout=90) as response:
                sheet = json.load(response)["sheet"]
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise SystemExit(f"desk: stopped while waiting for feedback: {exc}")
        if sheet is not None:
            return sheet


def _minutes(minutes: float) -> str:
    if minutes < 1:
        return f"{minutes * 60:g} seconds"
    return f"{minutes:g} minute" + ("" if minutes == 1 else "s")


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
    comments = [c for c in sheet.get("comments", []) if c.get("resolved_at") is None and c["version"] == sheet["version"]]
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
    feedback.add_argument(
        "path",
        nargs="?",
        help="one sheet's figure (default: every sheet from this directory with open comments, newest first)",
    )
    feedback.add_argument(
        "--all", action="store_true", help="every sheet on the desk with open comments, not only this directory's"
    )
    feedback.add_argument(
        "--in",
        dest="directory",
        default=None,
        help="the directory to resolve a relative path from (default: $DESK_CWD, then the cwd)",
    )
    feedback.add_argument("--json", action="store_true", help="print the report as one JSON object")
    feedback.add_argument(
        "--wait",
        nargs="?",
        const=DEFAULT_WAIT_MINUTES,
        default=None,
        type=float,
        metavar="MINUTES",
        help=(
            "hold until the user presses the feedback button on a sheet in scope, then report "
            f"that sheet; give up after MINUTES (default {DEFAULT_WAIT_MINUTES:g})"
        ),
    )
    feedback.add_argument(
        "--then",
        metavar="COMMAND",
        default=None,
        help=(
            "instead of printing, hand the report to this shell command on stdin, with the sheet's "
            "path in $DESK_SHEET; for a harness that cannot hear a background command finish"
        ),
    )
    feedback.set_defaults(func=cmd_feedback)

    export = sub.add_parser(
        "export", help="write the comments and versions of sheets to a directory another agent can read"
    )
    export.add_argument(
        "path",
        nargs="?",
        help="one sheet's figure (default: every commented sheet from this directory, newest first)",
    )
    export.add_argument(
        "--all", action="store_true", help="every commented sheet on the desk, not only this directory's"
    )
    export.add_argument(
        "--in",
        dest="directory",
        default=None,
        help="the directory to resolve from (default: $DESK_CWD, then the cwd)",
    )
    export.add_argument(
        "--out",
        metavar="DIR",
        default=None,
        help="where to write the bundle (default: desk-export-<timestamp> in that directory); must be new or empty",
    )
    export.set_defaults(func=cmd_export)

    sub.add_parser("status", help="report where the desk is and what is on it").set_defaults(func=cmd_status)

    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 2
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

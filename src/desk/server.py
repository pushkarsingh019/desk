"""The HTTP API — the desk's only seam to the outside world.

Publish, desk state, sheet content, layout transitions, the SSE stream, and the
static page. Everything a user or an agent can observe passes through here, and
so does every test in `tests/test_api.py`.
"""

from __future__ import annotations

import json
import math
import os
import queue
import re
import shutil
import socketserver
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from desk import layout as layout_model
from desk import render
from desk.store import PublishError, Store
from desk.watcher import DEFAULT_DEBOUNCE, DEFAULT_POLL_INTERVAL, Watcher

WEB_ROOT = Path(__file__).resolve().parent / "web"
DEFAULT_PORT = 7777
DEFAULT_DATA_DIR = Path.home() / ".desk"

#: Tailscale hands out addresses from the CGNAT range.
_TAILSCALE_RANGE = re.compile(r"^100\.(6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.")

STATIC_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
}


class BadRequest(ValueError):
    """A request the desk cannot act on as written.

    Distinct from `PublishError`, which means one thing only: a *file* the desk
    will not accept. Keeping them apart matters because the words are load
    bearing — CONTEXT.md gives `publish` a precise meaning, and a malformed
    Content-Length is not a publish.
    """


# --- events ---------------------------------------------------------------


class EventBus:
    """Fan-out to every open SSE subscriber."""

    def __init__(self):
        self._lock = threading.Lock()
        self._subscribers: set[queue.Queue] = set()

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=256)
        with self._lock:
            self._subscribers.add(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            self._subscribers.discard(q)

    def publish(self, event: dict) -> None:
        with self._lock:
            targets = list(self._subscribers)
        for q in targets:
            try:
                q.put_nowait(event)
            except queue.Full:
                pass


# --- the desk -------------------------------------------------------------


class Desk:
    """Store plus layout plus watcher — the whole server, minus HTTP."""

    def __init__(
        self,
        data_dir: Path,
        debounce: float = DEFAULT_DEBOUNCE,
        poll_interval: float = DEFAULT_POLL_INTERVAL,
    ):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.store = Store(self.data_dir)
        self.layout_path = self.data_dir / "layout.json"
        self._layout_lock = threading.RLock()
        self.layout = self._load_layout()
        self._reconcile()
        self.bus = EventBus()
        self.watcher = Watcher(
            self.store.watched_paths,
            self._on_source_changed,
            poll_interval=poll_interval,
            debounce=debounce,
        )

    # -- layout persistence ----------------------------------------------

    def _load_layout(self) -> dict:
        if not self.layout_path.exists():
            return layout_model.empty_state()
        try:
            state = json.loads(self.layout_path.read_text())
        except (json.JSONDecodeError, UnicodeDecodeError, OSError, ValueError):
            return layout_model.empty_state()
        return layout_model.repair(state)

    def _save_layout(self) -> None:
        tmp = self.layout_path.with_suffix(".json.writing")
        # allow_nan is off on purpose: `NaN` is not JSON, and a layout the
        # browser cannot parse is a blank desk with no way back.
        tmp.write_text(json.dumps(self.layout, indent=2, allow_nan=False))
        tmp.replace(self.layout_path)

    def _reconcile(self) -> None:
        """After a restart, make the layout agree with the store: every live
        sheet has a place (a new one lands in the inbox), and nothing lingers
        for a sheet that is gone."""
        with self._layout_lock:
            live = [s["id"] for s in self.store.live_sheets()]
            state = layout_model.prune(self.layout, live)
            for sheet_id in live:
                state = layout_model.add_sheet(state, sheet_id)
            self.layout = state
            self._save_layout()

    # -- publishing -------------------------------------------------------

    def publish(self, source_path: str) -> dict:
        sheet = self.store.publish(source_path)
        return self._land(sheet)

    def _on_source_changed(self, source_path: str) -> None:
        sheet = self.store.ingest_change(source_path)
        if sheet is not None:
            self._land(sheet)

    def _land(self, sheet: dict) -> dict:
        """Give a freshly published sheet a home and tell the page about it.

        Whether the page has to add the sheet or swap its image in place is a
        question about the layout, not about the store: the layout holds an
        entry for exactly the sheets the page is showing, and `_reconcile`
        keeps it that way across a restart.
        """
        with self._layout_lock:
            known = sheet["id"] in self.layout["sheets"]
            if not known:
                self.layout = layout_model.add_sheet(self.layout, sheet["id"])
                self._save_layout()
        payload = self.sheet_json(sheet)
        if known:
            self.bus.publish({"type": "sheet.version", "sheet": payload})
        else:
            self.bus.publish(
                {
                    "type": "sheet.created",
                    "sheet": payload,
                    "layout": self.layout_json(),
                    "geometry": self.geometry_json(),
                }
            )
        return payload

    # -- trash ------------------------------------------------------------

    def trash(self, sheet_id: str) -> dict | None:
        sheet = self.store.trash(sheet_id)
        if sheet is None:
            return None
        with self._layout_lock:
            self.layout = layout_model.remove_sheet(self.layout, sheet_id)
            self._save_layout()
        self.bus.publish(
            {
                "type": "sheet.trashed",
                "sheet_id": sheet_id,
                "layout": self.layout_json(),
                "geometry": self.geometry_json(),
            }
        )
        return self.sheet_json(sheet)

    def restore(self, sheet_id: str) -> dict | None:
        sheet = self.store.restore(sheet_id)
        if sheet is None:
            return None
        with self._layout_lock:
            self.layout = layout_model.add_sheet(self.layout, sheet_id)
            self._save_layout()
        payload = self.sheet_json(sheet)
        self.bus.publish(
            {
                "type": "sheet.restored",
                "sheet": payload,
                "layout": self.layout_json(),
                "geometry": self.geometry_json(),
            }
        )
        return payload

    # -- layout -----------------------------------------------------------

    def apply_layout(self, op: str, params: dict) -> dict:
        with self._layout_lock:
            self.layout = apply_layout_op(self.layout, op, params)
            self._save_layout()
            return self.layout_json()

    def layout_json(self) -> dict:
        with self._layout_lock:
            return json.loads(json.dumps(self.layout))

    # -- reading ----------------------------------------------------------

    def sheet_json(self, sheet: dict) -> dict:
        versions = [v["n"] for v in sheet["versions"]]
        latest = versions[-1] if versions else 0
        return {
            "id": sheet["id"],
            "source_path": sheet["source_path"],
            "name": Path(sheet["source_path"]).name,
            "kind": sheet["kind"],
            "version": latest,
            "versions": versions,
            "content_url": f"/api/content/{sheet['id']}/{latest}",
            "created_at": sheet["created_at"],
            "updated_at": sheet["updated_at"],
            "trashed": sheet["trashed"],
        }

    def geometry_json(self) -> dict:
        """What the page and the overview both have to work out from the layout.

        Derived, never persisted: `layout.json` stays the plain state object.
        It is computed here rather than in the page so that there is one
        implementation of desk geometry instead of two that drift apart.
        """
        with self._layout_lock:
            box = layout_model.bounds(self.layout)
        return {
            "bounds": box,
            "fan_step": {"x": layout_model.FAN_STEP_X, "y": layout_model.FAN_STEP_Y},
            "default_size": {
                "w": layout_model.DEFAULT_WIDTH,
                "h": layout_model.DEFAULT_HEIGHT,
            },
            "min_size": layout_model.MIN_SIZE,
        }

    def state_json(self) -> dict:
        return {
            "sheets": [self.sheet_json(s) for s in self.store.live_sheets()],
            "trash": [self.sheet_json(s) for s in self.store.trashed_sheets()],
            "layout": self.layout_json(),
            "geometry": self.geometry_json(),
            "data_dir": str(self.data_dir),
        }

    def content(self, sheet_id: str, version: int | None):
        data, content_type = self.store.content(sheet_id, version)
        sheet = self.store.get(sheet_id)
        if sheet and sheet["kind"] == "md":
            data = render.markdown_to_page(data)
        elif sheet and sheet["kind"] == "html":
            try:
                data.decode("utf-8")
            except UnicodeDecodeError as exc:
                data = render.unreadable_page("This HTML file is not valid UTF-8", str(exc))
        return data, content_type

    # -- lifetime ---------------------------------------------------------

    def start(self) -> None:
        self.watcher.start()

    def stop(self) -> None:
        self.watcher.stop()


def _number(params: dict, key: str) -> float:
    """One coordinate off the wire.

    Every number the page sends is checked here rather than in the layout
    model, because this is where untrusted JSON stops being untrusted. `NaN`
    and `Infinity` matter most: `json.loads` accepts both, `JSON.parse` accepts
    neither, so one of them reaching `layout.json` would leave the user with a
    desk that never renders again and no way to undo it from the page.
    """
    value = params.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise BadRequest(f"{key} must be a finite number, not {value!r}")
    return float(value)


def _identifier(params: dict, key: str) -> str:
    value = params.get(key)
    if not isinstance(value, str):
        raise BadRequest(f"{key} must be a sheet or pile id, not {value!r}")
    return value


LAYOUT_OPS = {
    "place": lambda s, p: layout_model.place(
        s, _identifier(p, "sheet_id"), _number(p, "x"), _number(p, "y")
    ),
    "move": lambda s, p: layout_model.move(
        s, _identifier(p, "sheet_id"), _number(p, "x"), _number(p, "y")
    ),
    "resize": lambda s, p: layout_model.resize(
        s, _identifier(p, "sheet_id"), _number(p, "w"), _number(p, "h")
    ),
    "raise": lambda s, p: layout_model.raise_sheet(s, _identifier(p, "sheet_id")),
    "pile": lambda s, p: layout_model.pile(
        s, _identifier(p, "sheet_id"), _identifier(p, "onto")
    ),
    "unpile": lambda s, p: layout_model.unpile(
        s, _identifier(p, "sheet_id"), _number(p, "x"), _number(p, "y")
    ),
    "move_pile": lambda s, p: layout_model.move_pile(
        s, _identifier(p, "pile_id"), _number(p, "x"), _number(p, "y")
    ),
    "toggle_pile": lambda s, p: layout_model.toggle_pile(s, _identifier(p, "pile_id")),
    "close_piles": lambda s, p: layout_model.close_all_piles(s),
    "viewport": lambda s, p: layout_model.set_viewport(
        s, _number(p, "x"), _number(p, "y"), _number(p, "scale")
    ),
}


def apply_layout_op(state: dict, op, params: dict) -> dict:
    try:
        transition = LAYOUT_OPS[op]
    except (KeyError, TypeError):
        raise BadRequest(f"unknown layout op {op!r}") from None
    try:
        return transition(state, params)
    except KeyError as exc:
        raise BadRequest(f"{op}: {exc}") from None


# --- HTTP -----------------------------------------------------------------


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "desk"

    @property
    def desk(self) -> Desk:
        return self.server.desk

    def log_message(self, fmt, *args):
        sys.stderr.write("%s %s\n" % (self.address_string(), fmt % args))

    # -- responses --------------------------------------------------------

    def _send(self, status: int, body: bytes, content_type: str, extra: dict | None = None):
        headers = {
            "Content-Type": content_type,
            "Content-Length": str(len(body)),
            "Cache-Control": "no-store",
        }
        headers.update(extra or {})
        self.send_response(status)
        for key, value in headers.items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status: int, payload: dict):
        self._send(status, json.dumps(payload).encode("utf-8"), "application/json")

    def _error(self, status: int, message: str):
        self._json(status, {"error": message})

    def _body(self) -> dict:
        """The request body as an object. Anything else is no fields at all."""
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            # The body cannot be read, so the connection cannot be reused —
            # answering and keeping it open would desync the next request.
            self.close_connection = True
            raise BadRequest("Content-Length is not a number") from None
        if length <= 0:
            return {}
        try:
            body = json.loads(self.rfile.read(length))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}
        return body if isinstance(body, dict) else {}

    # -- routing ----------------------------------------------------------

    def do_GET(self):
        path = unquote(urlparse(self.path).path)
        try:
            if path == "/api/state":
                return self._json(200, self.desk.state_json())
            if path == "/api/events":
                return self._events()
            if path.startswith("/api/content/"):
                return self._content(path)
            if path.startswith("/api/"):
                return self._error(404, f"no such endpoint: {path}")
            return self._static(path)
        except BrokenPipeError:
            pass
        except Exception as exc:  # never let one bad request take the desk down
            self.log_error("%s", exc)
            try:
                self._error(500, str(exc))
            except Exception:
                pass

    def do_HEAD(self):
        if unquote(urlparse(self.path).path) == "/api/events":
            # There is no such thing as the head of an endless stream, and
            # answering one would hold a thread open until a write failed.
            return self._error(405, "the event stream cannot be fetched with HEAD")
        self.do_GET()

    def do_POST(self):
        try:
            path = unquote(urlparse(self.path).path)
            body = self._body()
            if path == "/api/publish":
                return self._publish(body)
            if path == "/api/layout":
                return self._layout(body)
            if path == "/api/trash":
                return self._trash(body)
            if path == "/api/restore":
                return self._restore(body)
            return self._error(404, f"no such endpoint: {path}")
        except BrokenPipeError:
            pass
        except (BadRequest, PublishError) as exc:
            return self._error(400, str(exc))
        except Exception as exc:
            self.log_error("%s", exc)
            try:
                self._error(500, str(exc))
            except Exception:
                pass

    # -- endpoints --------------------------------------------------------

    def _publish(self, body: dict):
        source_path = body.get("source_path") or body.get("path")
        if not isinstance(source_path, str) or not source_path.strip():
            return self._error(400, f"publish needs a source_path, not {source_path!r}")
        try:
            sheet = self.desk.publish(source_path)
        except PublishError as exc:
            return self._error(400, str(exc))
        return self._json(200, {"sheet": sheet, "desk_url": self.server.desk_url})

    def _layout(self, body: dict):
        op = body.get("op")
        if not op:
            return self._error(400, "layout needs an op")
        try:
            state = self.desk.apply_layout(op, body)
        except (BadRequest, layout_model.LayoutError) as exc:
            return self._error(400, str(exc))
        return self._json(200, {"layout": state, "geometry": self.desk.geometry_json()})

    def _trash(self, body: dict):
        sheet_id = _identifier(body, "sheet_id")
        sheet = self.desk.trash(sheet_id)
        if sheet is None:
            return self._error(404, f"no sheet {sheet_id!r}")
        return self._json(200, {"sheet": sheet})

    def _restore(self, body: dict):
        sheet_id = _identifier(body, "sheet_id")
        sheet = self.desk.restore(sheet_id)
        if sheet is None:
            return self._error(404, f"no sheet {sheet_id!r}")
        return self._json(200, {"sheet": sheet})

    def _content(self, path: str):
        parts = [part for part in path[len("/api/content/") :].split("/") if part]
        if not parts:
            return self._error(404, "no content for that sheet")
        sheet_id = parts[0]
        version = None
        if len(parts) > 1:
            try:
                version = int(parts[1])
            except ValueError:
                return self._error(404, f"sheet {sheet_id!r} has no version {parts[1]!r}")
        try:
            data, content_type = self.desk.content(sheet_id, version)
        except (KeyError, OSError):
            return self._error(404, f"no content for sheet {sheet_id!r}")
        # A named version's content never changes, so it may be cached hard: a
        # new version arrives at a new URL. The unversioned URL follows the
        # sheet, so caching it would freeze a sheet on an old picture forever.
        cache = "public, max-age=31536000, immutable" if version is not None else "no-store"
        self._send(200, data, content_type, extra={"Cache-Control": cache})

    def _events(self):
        q = self.desk.bus.subscribe()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        try:
            self.wfile.write(b"retry: 1000\n\n")
            self.wfile.flush()
            while True:
                try:
                    event = q.get(timeout=15)
                except queue.Empty:
                    self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
                    continue
                payload = json.dumps(event).encode("utf-8")
                self.wfile.write(b"event: " + event["type"].encode() + b"\n")
                self.wfile.write(b"data: " + payload + b"\n\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            self.desk.bus.unsubscribe(q)

    def _static(self, path: str):
        rel = "index.html" if path in ("/", "") else path.lstrip("/")
        target = (WEB_ROOT / rel).resolve()
        # is_relative_to, not a string prefix: "…/webbing" starts with "…/web".
        if not target.is_relative_to(WEB_ROOT) or not target.is_file():
            return self._error(404, f"no such file: {path}")
        suffix = target.suffix.lower()
        self._send(200, target.read_bytes(), STATIC_TYPES.get(suffix, "application/octet-stream"))


class DeskServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, desk: Desk, desk_url: str):
        super().__init__(address, Handler)
        self.desk = desk
        self.desk_url = desk_url

    def server_bind(self):
        """Bind without the reverse DNS lookup the stdlib does here.

        `HTTPServer.server_bind` calls `socket.getfqdn()` between binding and
        listening. Under launchd that lookup blocks indefinitely, leaving the
        desk bound but never accepting — running, silent, and unreachable.
        The name it computes is only ever used to fill in CGI variables.
        """
        socketserver.TCPServer.server_bind(self)
        self.server_name = self.server_address[0]
        self.server_port = self.server_address[1]


# --- binding --------------------------------------------------------------


#: Where the Tailscale CLI lives when it is not on PATH. Under launchd PATH is
#: minimal and the macOS app bundle is the only copy on the machine, so the
#: list is not redundant with `which`.
TAILSCALE_CLIS = (
    "/Applications/Tailscale.app/Contents/MacOS/Tailscale",
    "/usr/bin/tailscale",
    "/usr/local/bin/tailscale",
    "/opt/homebrew/bin/tailscale",
    r"C:\Program Files\Tailscale\tailscale.exe",
)

#: The address the desk falls back to when there is no tailnet. Not `0.0.0.0`:
#: the desk has no TLS and no login, so the address it binds is its only
#: perimeter, and localhost is a perimeter even on a cafe network.
LOCAL_BIND = "127.0.0.1"


def tailscale_cli() -> str | None:
    """The Tailscale command, or None if this machine has no Tailscale."""
    override = os.environ.get("DESK_TAILSCALE_CLI")
    if override:
        return override if os.path.exists(override) else None
    found = shutil.which("tailscale")
    if found:
        return found
    for candidate in TAILSCALE_CLIS:
        if os.path.exists(candidate):
            return candidate
    return None


def _tailscale_status() -> dict | None:
    cli = tailscale_cli()
    if not cli:
        return None
    try:
        out = subprocess.run(
            [cli, "status", "--json"], capture_output=True, text=True, timeout=5
        )
        return json.loads(out.stdout)
    except Exception:
        return None


def tailscale_name(fallback: str) -> str:
    """The machine's name on the tailnet, for the URL the user opens.

    Deliberately not `socket.getfqdn()`: that does a reverse lookup, which
    returns an `ip6.arpa` string here and can block for minutes under launchd,
    hanging the desk before it ever binds.
    """
    status = _tailscale_status()
    try:
        name = status["Self"]["DNSName"].rstrip(".")
    except (TypeError, KeyError, AttributeError):
        name = ""
    return name or fallback


def _interface_addresses() -> list[str]:
    """Every IPv4 address the platform will name, however it names them.

    Asked only when the Tailscale CLI is unreachable, which is the normal case
    under launchd. Each command is tried in turn and the first that runs wins;
    a machine has either `ifconfig` or `ip`, never a reason to run both.
    """
    for command in (["/sbin/ifconfig"], ["ifconfig"], ["ip", "-4", "-o", "addr"]):
        try:
            out = subprocess.run(
                command, capture_output=True, text=True, timeout=5
            ).stdout
        except (OSError, subprocess.SubprocessError):
            continue
        if out:
            return re.findall(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", out)
    return []


def tailscale_address() -> str | None:
    """The machine's address on the tailnet, or None if it isn't up."""
    status = _tailscale_status()
    if status:
        for addr in (status.get("Self") or {}).get("TailscaleIPs") or []:
            if _TAILSCALE_RANGE.match(addr):
                return addr
    for addr in _interface_addresses():
        if _TAILSCALE_RANGE.match(addr):
            return addr
    return None


class BindError(RuntimeError):
    """There is no address the desk may bind to, so it will not start."""


class NoTailnet(BindError):
    """Tailscale is not up, and the desk was told to bind there and nowhere else."""


def resolve_host(wait: float | None = None) -> tuple[str, str]:
    """Return (bind address, the hostname to print in the desk URL).

    The address the desk binds is its only perimeter — there is no TLS and no
    login — so this never widens on its own. It answers in one of three ways:

    * `DESK_HOST` — bind exactly there. The escape hatch, and it wins.
    * `DESK_BIND=tailnet` — the tailnet address and nothing else, waiting for
      Tailscale to come up. `install.sh` writes this when it finds a tailnet,
      so a desk browsed from another machine never quietly retreats to
      localhost after a reboot and leaves its URL dead.
    * `DESK_BIND=local` — `127.0.0.1`, for a desk browsed on the machine that
      serves it.
    * Nothing set — the tailnet if it is already there, otherwise localhost.
    """
    override = os.environ.get("DESK_HOST")
    if override:
        return override, os.environ.get("DESK_HOSTNAME", override)

    mode = (os.environ.get("DESK_BIND") or "auto").strip().lower()
    if mode not in ("auto", "tailnet", "local"):
        raise BindError(f"DESK_BIND must be auto, tailnet or local; got {mode!r}")
    if mode == "local":
        return LOCAL_BIND, os.environ.get("DESK_HOSTNAME") or "localhost"

    # Only `tailnet` waits. At login the desk may well start before Tailscale
    # does, and a desk that is meant to be reachable from another machine
    # should stall rather than come up at the wrong address.
    if wait is None:
        wait = float(os.environ.get("DESK_TAILNET_WAIT") or (45 if mode == "tailnet" else 0))
    deadline = time.monotonic() + wait
    while True:
        tailnet = tailscale_address()
        if tailnet:
            # Ask Tailscale first so a rename is picked up, and fall back to
            # the name install.sh baked in — under launchd the Tailscale CLI is
            # not reachable, which is the whole reason that value exists.
            return tailnet, tailscale_name(os.environ.get("DESK_HOSTNAME") or tailnet)
        if time.monotonic() >= deadline:
            break
        time.sleep(2)

    if mode == "tailnet":
        raise NoTailnet(
            "no tailscale address found. This desk was installed to bind to the "
            "tailnet and nothing else, so it will not start without one. Bring "
            "Tailscale up, or set DESK_BIND=local to serve this machine only."
        )
    return LOCAL_BIND, os.environ.get("DESK_HOSTNAME") or "localhost"


def build() -> tuple[DeskServer, str]:
    data_dir = Path(os.environ.get("DESK_DATA_DIR") or DEFAULT_DATA_DIR).expanduser()
    port = int(os.environ.get("DESK_PORT") or DEFAULT_PORT)
    debounce = float(os.environ.get("DESK_DEBOUNCE") or DEFAULT_DEBOUNCE)
    poll = float(os.environ.get("DESK_POLL_INTERVAL") or DEFAULT_POLL_INTERVAL)
    bind, hostname = resolve_host()
    desk = Desk(data_dir, debounce=debounce, poll_interval=poll)
    url = f"http://{hostname}:{port}"
    return DeskServer((bind, port), desk, url), url


def main() -> int:
    try:
        server, url = build()
    except BindError as exc:
        # Exit nonzero rather than bind somewhere the user did not ask for.
        # Under launchd this means "try again in a moment", which is exactly
        # what is wanted when the desk starts before Tailscale does.
        sys.stderr.write(f"desk: {exc}\n")
        return 1
    server.desk.start()
    sys.stderr.write(
        f"desk: data in {server.desk.data_dir}, "
        f"listening on {server.server_address[0]}:{server.server_address[1]}\n"
    )
    sys.stderr.write(f"desk: {url}\n")
    sys.stderr.flush()
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        server.desk.stop()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

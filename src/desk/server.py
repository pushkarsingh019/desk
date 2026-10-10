"""The HTTP API — the desk's only seam to the outside world.

Publish, desk state, sheet content, layout transitions, comments, the SSE
stream, and the static page. Everything a user or an agent can observe passes through here, and
so does every test in `tests/test_api.py`.
"""

from __future__ import annotations

import base64
import binascii
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
from urllib.parse import parse_qs, quote, unquote, urlparse

from desk import layout as layout_model
from desk import render
from desk.store import CommentError, PublishError, Store, is_under
from desk.watcher import DEFAULT_DEBOUNCE, DEFAULT_POLL_INTERVAL, Watcher

WEB_ROOT = Path(__file__).resolve().parent / "web"
DEFAULT_PORT = 7777
#: The longest one feedback wait is held open. The command re-asks.
WAIT_TIMEOUT = 60.0
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
    """One desk: its store and its layout, in a directory of its own.

    A desk knows nothing about the others. It reports what happens on it to
    `emit`, and `Desks` decides whether the page — which shows exactly one
    desk — needs to hear about it.
    """

    def __init__(self, name: str, root: Path, emit):
        self.name = name
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.store = Store(self.root)
        self.layout_path = self.root / "layout.json"
        self._layout_lock = threading.RLock()
        self.layout = self._load_layout()
        self._reconcile()
        self._emit = emit
        # Agents waiting for the user to ask for feedback: each a queue of
        # one, with the scope it listens for. Nothing here is persisted — a
        # waiter is a held connection, and it ends with the connection.
        self._waiters_lock = threading.Lock()
        self._waiters: list[tuple[dict, queue.Queue]] = []

    def emit(self, event: dict) -> None:
        event["desk"] = self.name
        self._emit(self.name, event)

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

    def receive(self, origin, source_path, data: bytes) -> dict:
        """A file sent from another machine. Lands like any other publish."""
        sheet = self.store.receive(origin, source_path, data)
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
            self.emit({"type": "sheet.version", "sheet": payload})
        else:
            self.emit(
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
        self.emit(
            {
                "type": "sheet.trashed",
                "sheet_id": sheet_id,
                "layout": self.layout_json(),
                "geometry": self.geometry_json(),
            }
        )
        return self.sheet_json(sheet)

    def clear(self) -> list[str]:
        """Throw every live sheet away — desk, inbox, and piles — in one go.

        Each one is tombstoned exactly as if trashed by hand, so the trash can
        bring any of them back."""
        trashed = [s["id"] for s in self.store.live_sheets() if self.store.trash(s["id"])]
        with self._layout_lock:
            self.layout = layout_model.prune(self.layout, [])
            self._save_layout()
        self.emit(
            {
                "type": "desk.cleared",
                "trashed": trashed,
                "layout": self.layout_json(),
                "geometry": self.geometry_json(),
            }
        )
        return trashed

    def restore(self, sheet_id: str) -> dict | None:
        sheet = self.store.restore(sheet_id)
        if sheet is None:
            return None
        with self._layout_lock:
            self.layout = layout_model.add_sheet(self.layout, sheet_id)
            self._save_layout()
        payload = self.sheet_json(sheet)
        self.emit(
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

    # -- comments ---------------------------------------------------------

    def comment(self, op, params: dict) -> dict:
        """Add, resolve, or remove a comment on one sheet.

        A comment is a record on the sheet, not layout: the event that goes
        out carries the sheet and nothing else, so every open page redraws
        its pins and nothing on any desk can move.
        """
        if op == "add":
            sheet = self.store.add_comment(
                params.get("sheet_id"), params.get("anchor"), params.get("text")
            )
        elif op == "resolve":
            sheet = self.store.resolve_comment(params.get("sheet_id"), params.get("comment_id"))
        elif op == "remove":
            sheet = self.store.remove_comment(params.get("sheet_id"), params.get("comment_id"))
        else:
            raise BadRequest(f"comments needs an op of add, resolve or remove, not {op!r}")
        payload = self.sheet_json(sheet)
        self.emit({"type": "sheet.changed", "sheet": payload})
        return payload

    # -- feedback ---------------------------------------------------------

    def wait_for_feedback(self, scope: dict, timeout: float) -> dict | None:
        """Hold until the user asks for feedback on a sheet in `scope`, and
        return that sheet, or None when nobody asked within `timeout`.

        The scope is the one `desk feedback` reads with: `path` is one sheet,
        `under` is every local sheet whose file is under that directory, and
        neither is the whole desk. Reading only: waiting changes nothing on
        the sheet and never resolves a comment.
        """
        q: queue.Queue = queue.Queue(maxsize=1)
        entry = (scope, q)
        with self._waiters_lock:
            self._waiters.append(entry)
        try:
            return q.get(timeout=timeout)
        except queue.Empty:
            return None
        finally:
            with self._waiters_lock:
                if entry in self._waiters:
                    self._waiters.remove(entry)

    def request_feedback(self, sheet_id) -> dict:
        """The user asked, from the page, for the agent to read a sheet's
        comments. Wake every waiter whose scope holds the sheet and say how
        many there were, so the page can tell the user whether anyone heard."""
        sheet = self.store.get(sheet_id) if isinstance(sheet_id, str) else None
        if sheet is None or sheet["trashed"]:
            raise BadRequest(f"no live sheet {sheet_id!r} on this desk")
        payload = self.sheet_json(sheet)
        if not payload["open_comments"]:
            raise BadRequest(f"no open comments on {payload['name']}: pin one first")
        woken = 0
        with self._waiters_lock:
            for scope, q in list(self._waiters):
                if not _in_scope(payload, scope):
                    continue
                try:
                    q.put_nowait(payload)
                    woken += 1
                except queue.Full:
                    pass
                self._waiters.remove((scope, q))
        return {"sheet": payload, "waiters": woken}

    # -- reading ----------------------------------------------------------

    def sheet_json(self, sheet: dict) -> dict:
        versions = [v["n"] for v in sheet["versions"]]
        latest = versions[-1] if versions else 0
        comments = [
            self.comment_json(sheet, comment, number)
            for number, comment in enumerate(sheet.get("comments", []), start=1)
        ]
        return {
            "id": sheet["id"],
            "source_path": sheet["source_path"],
            "origin": sheet.get("origin"),
            "name": Path(sheet["source_path"]).name,
            "kind": sheet["kind"],
            "version": latest,
            "versions": versions,
            # Names the desk as well as the sheet: sheet ids are per desk, and
            # a versioned URL is cached forever, so the same path on two desks
            # must never share one.
            "content_url": f"/api/content/{quote(self.name, safe='')}/{sheet['id']}/{latest}",
            "created_at": sheet["created_at"],
            "updated_at": sheet["updated_at"],
            "trashed": sheet["trashed"],
            # The content's natural box, read from the stored bytes, so that
            # the page and the `desk` command never have to measure anything.
            "natural_size": self.store.natural_size(sheet["id"]),
            "comments": comments,
            "open_comments": sum(1 for c in comments if c["resolved_at"] is None),
        }

    def comment_json(self, sheet: dict, comment: dict, number: int) -> dict:
        """One comment as the page and the `desk` command see it: the record,
        its number (its place among every comment on the sheet, so the pin
        and the report agree), and the natural box of the version it was
        made on — None once that version has been evicted."""
        return {
            **comment,
            "number": number,
            "natural_size": self.store.natural_size(sheet["id"], comment["version"]),
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
        }

    def is_empty(self) -> bool:
        return not self.store.live_sheets()

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


#: What a desk may be called: something that survives a URL and a directory
#: name unchanged, so the name on the page is the name on disk.
DESK_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._ -]{0,39}$")
DEFAULT_DESK = "main"


class Desks:
    """Every desk this server keeps, and which one is current.

    Exactly one desk is current: it is the one the page shows and the one
    everything lands on — a `/desk`, a send, a restore. The others are still
    there, still watched, still updating; switching brings one of them to the
    front. Desks live under `<data_dir>/desks/<name>/`, one directory each,
    and the directory listing is the registry.
    """

    def __init__(
        self,
        data_dir: Path,
        debounce: float = DEFAULT_DEBOUNCE,
        poll_interval: float = DEFAULT_POLL_INTERVAL,
    ):
        self.data_dir = Path(data_dir)
        self.desks_dir = self.data_dir / "desks"
        self.current_path = self.data_dir / "current.json"
        self.desks_dir.mkdir(parents=True, exist_ok=True)
        self.bus = EventBus()
        self._lock = threading.RLock()
        self._migrate_single_desk()
        self._desks: dict[str, Desk] = {}
        for entry in sorted(self.desks_dir.iterdir()):
            if entry.is_dir() and DESK_NAME.match(entry.name):
                self._desks[entry.name] = Desk(entry.name, entry, self._emit)
        if not self._desks:
            self._desks[DEFAULT_DESK] = Desk(DEFAULT_DESK, self.desks_dir / DEFAULT_DESK, self._emit)
        self.current_name = self._load_current()
        self.watcher = Watcher(
            self._watched_paths,
            self._on_source_changed,
            poll_interval=poll_interval,
            debounce=debounce,
        )

    # -- persistence ------------------------------------------------------

    def _migrate_single_desk(self) -> None:
        """A data directory from before there were several desks holds one
        desk at its root. It becomes the `main` desk, moved, not copied, so
        nothing is ever in two places."""
        target = self.desks_dir / DEFAULT_DESK
        for piece in ("sheets.json", "layout.json", "content"):
            old = self.data_dir / piece
            if old.exists() and not (target / piece).exists():
                target.mkdir(parents=True, exist_ok=True)
                old.rename(target / piece)

    def _load_current(self) -> str:
        try:
            name = json.loads(self.current_path.read_text()).get("current")
        except (OSError, ValueError, AttributeError):
            name = None
        if name in self._desks:
            return name
        return DEFAULT_DESK if DEFAULT_DESK in self._desks else sorted(self._desks)[0]

    def _save_current(self) -> None:
        tmp = self.current_path.with_suffix(".json.writing")
        tmp.write_text(json.dumps({"current": self.current_name}))
        tmp.replace(self.current_path)

    # -- which desk -------------------------------------------------------

    @property
    def current(self) -> Desk:
        with self._lock:
            return self._desks[self.current_name]

    def names(self) -> list[str]:
        with self._lock:
            return sorted(self._desks)

    def get(self, name: str) -> Desk:
        with self._lock:
            return self._desks[name]

    def create(self, name) -> dict:
        """Make a new, empty desk and bring it to the front."""
        name = _desk_name(name)
        with self._lock:
            if name in self._desks:
                raise BadRequest(f"there is already a desk called {name!r}")
            self._desks[name] = Desk(name, self.desks_dir / name, self._emit)
        return self.switch(name)

    def switch(self, name) -> dict:
        name = _desk_name(name)
        with self._lock:
            if name not in self._desks:
                raise BadRequest(f"there is no desk called {name!r}")
            self.current_name = name
            self._save_current()
        return self._changed()

    def remove(self, name) -> dict:
        """Take an empty desk away. Its trash goes with it; a desk with
        anything still on it is refused, and so is the last desk."""
        name = _desk_name(name)
        with self._lock:
            desk = self._desks.get(name)
            if desk is None:
                raise BadRequest(f"there is no desk called {name!r}")
            if not desk.is_empty():
                raise BadRequest(f"the desk {name!r} still has sheets on it; clear it first")
            if len(self._desks) == 1:
                raise BadRequest("that is the only desk; there has to be one")
            del self._desks[name]
            shutil.rmtree(desk.root, ignore_errors=True)
            if self.current_name == name:
                self.current_name = self._load_current()
                self._save_current()
        return self._changed()

    def _changed(self) -> dict:
        summary = {"desk": self.current_name, "desks": self.names()}
        self.bus.publish({"type": "desk.changed", **summary})
        return summary

    # -- events -----------------------------------------------------------

    def _emit(self, desk_name: str, event: dict) -> None:
        """The page shows the current desk and nothing else, so only that
        desk's events reach it. Whatever happened elsewhere is in the state
        the page fetches when it switches there."""
        if desk_name == self.current_name:
            self.bus.publish(event)

    # -- watching ---------------------------------------------------------

    def _watched_paths(self) -> list[str]:
        """Every desk's live paths: a sheet keeps updating whether or not its
        desk is the one on screen."""
        with self._lock:
            desks = list(self._desks.values())
        paths: set[str] = set()
        for desk in desks:
            paths.update(desk.store.watched_paths())
        return sorted(paths)

    def _on_source_changed(self, source_path: str) -> None:
        with self._lock:
            desks = list(self._desks.values())
        for desk in desks:
            if source_path in desk.store.watched_paths():
                desk._on_source_changed(source_path)

    # -- reading ----------------------------------------------------------

    def state_json(self) -> dict:
        with self._lock:
            desk = self.current
            state = desk.state_json()
        state["desk"] = desk.name
        state["desks"] = self.names()
        state["data_dir"] = str(self.data_dir)
        return state

    def content(self, desk_name: str, sheet_id: str, version: int | None):
        return self.get(desk_name).content(sheet_id, version)

    # -- lifetime ---------------------------------------------------------

    def start(self) -> None:
        self.watcher.start()

    def stop(self) -> None:
        self.watcher.stop()


def _in_scope(sheet: dict, scope: dict) -> bool:
    """The scope rules of `desk feedback`: a path names one sheet wherever it
    came from; a directory holds the local sheets whose files are under it;
    an empty scope is the whole desk."""
    if "path" in scope:
        return sheet["source_path"] == scope["path"]
    if "under" in scope:
        return sheet["origin"] is None and is_under(sheet["source_path"], scope["under"])
    return True


def _desk_name(name) -> str:
    if not isinstance(name, str) or not DESK_NAME.match(name.strip()) or name != name.strip():
        raise BadRequest(
            f"a desk name is letters, digits, spaces, dots, dashes or underscores, "
            f"up to 40 of them, not {name!r}"
        )
    return name


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


def _bytes(params: dict, key: str) -> bytes:
    """A file's bytes off the wire, carried as base64 inside the JSON body."""
    value = params.get(key)
    if not isinstance(value, str):
        raise BadRequest(f"{key} must be the file's bytes as base64, not {type(value).__name__}")
    try:
        return base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        raise BadRequest(f"{key} is not valid base64") from None


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
    def desks(self) -> Desks:
        return self.server.desks

    @property
    def desk(self) -> Desk:
        """The current desk — where everything lands."""
        return self.server.desks.current

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
                return self._json(200, self.desks.state_json())
            if path == "/api/events":
                return self._events()
            if path == "/api/feedback/wait":
                return self._wait_for_feedback(parse_qs(urlparse(self.path).query))
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
            if path == "/api/clear":
                return self._json(200, {"trashed": self.desk.clear()})
            if path == "/api/comments":
                return self._json(200, {"sheet": self.desk.comment(body.get("op"), body)})
            if path == "/api/feedback":
                return self._json(200, self.desk.request_feedback(body.get("sheet_id")))
            if path == "/api/desks":
                return self._desks(body)
            return self._error(404, f"no such endpoint: {path}")
        except BrokenPipeError:
            pass
        except (BadRequest, PublishError, CommentError) as exc:
            return self._error(400, str(exc))
        except Exception as exc:
            self.log_error("%s", exc)
            try:
                self._error(500, str(exc))
            except Exception:
                pass

    # -- endpoints --------------------------------------------------------

    def _publish(self, body: dict):
        """Hand a file to the desk: by path when it is on this machine, or as
        bytes when it was sent from another one. Same verb, two transports."""
        source_path = body.get("source_path") or body.get("path")
        if not isinstance(source_path, str) or not source_path.strip():
            return self._error(400, f"publish needs a source_path, not {source_path!r}")
        try:
            if "content" in body:
                sheet = self.desk.receive(body.get("origin"), source_path, _bytes(body, "content"))
            else:
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

    def _desks(self, body: dict):
        op = body.get("op")
        ops = {"create": self.desks.create, "switch": self.desks.switch, "remove": self.desks.remove}
        if op not in ops:
            return self._error(400, f"desks needs an op of create, switch or remove, not {op!r}")
        return self._json(200, ops[op](body.get("name")))

    def _content(self, path: str):
        # /api/content/<desk>/<sheet id>[/<version>]
        parts = [part for part in path[len("/api/content/") :].split("/") if part]
        if len(parts) < 2:
            return self._error(404, "no content for that sheet")
        desk_name, sheet_id = parts[0], parts[1]
        version = None
        if len(parts) > 2:
            try:
                version = int(parts[2])
            except ValueError:
                return self._error(404, f"sheet {sheet_id!r} has no version {parts[2]!r}")
        try:
            data, content_type = self.desks.content(desk_name, sheet_id, version)
        except (KeyError, OSError):
            return self._error(404, f"no content for sheet {sheet_id!r} on desk {desk_name!r}")
        # A named version's content never changes, so it may be cached hard: a
        # new version arrives at a new URL. The unversioned URL follows the
        # sheet, so caching it would freeze a sheet on an old picture forever.
        cache = "public, max-age=31536000, immutable" if version is not None else "no-store"
        self._send(200, data, content_type, extra={"Cache-Control": cache})

    def _wait_for_feedback(self, query: dict):
        """Long poll: answer when the user asks for feedback in scope, or with
        no sheet once `timeout` seconds have passed, and never before."""
        scope = {}
        if query.get("path"):
            scope["path"] = query["path"][0]
        elif query.get("under"):
            scope["under"] = query["under"][0]
        try:
            timeout = float(query.get("timeout", [WAIT_TIMEOUT])[0])
        except ValueError:
            return self._error(400, f"timeout must be seconds, not {query['timeout'][0]!r}")
        timeout = max(0.0, min(timeout, WAIT_TIMEOUT))
        # The desk that was current when the wait began: switching desks
        # moves where the next sheet lands, not what an agent is waiting on.
        sheet = self.desk.wait_for_feedback(scope, timeout)
        return self._json(200, {"sheet": sheet})

    def _events(self):
        q = self.desks.bus.subscribe()
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
            self.desks.bus.unsubscribe(q)

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

    def __init__(self, address, desks: Desks, desk_url: str):
        super().__init__(address, Handler)
        self.desks = desks
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
    desks = Desks(data_dir, debounce=debounce, poll_interval=poll)
    url = f"http://{hostname}:{port}"
    return DeskServer((bind, port), desks, url), url


def main() -> int:
    try:
        server, url = build()
    except BindError as exc:
        # Exit nonzero rather than bind somewhere the user did not ask for.
        # Under launchd this means "try again in a moment", which is exactly
        # what is wanted when the desk starts before Tailscale does.
        sys.stderr.write(f"desk: {exc}\n")
        return 1
    server.desks.start()
    sys.stderr.write(
        f"desk: data in {server.desks.data_dir}, "
        f"listening on {server.server_address[0]}:{server.server_address[1]}\n"
    )
    sys.stderr.write(f"desk: {url}\n")
    sys.stderr.flush()
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        server.desks.stop()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

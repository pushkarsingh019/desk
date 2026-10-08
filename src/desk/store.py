"""The store — the desk's own copy of every published file.

Publishing copies the file in and appends a version; it never references the
source in place, so deleting or clobbering the source cannot damage a sheet.

Sheet identity is the absolute source path, on the machine the file lives on.
Same path means same sheet, forever. A sheet sent from another machine carries
an origin, and its identity is the origin plus its path there. This one rule
is what makes update-in-place the default.

A sheet also carries the user's comments: records on the sheet, not layout,
so they ride with it through new versions, the trash, and a restart.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import secrets
import struct
import threading
import time
import unicodedata
from pathlib import Path

#: A file may reach the desk only if its extension is on this list.
ALLOWED_EXTENSIONS = {".svg", ".png", ".pdf", ".html", ".md"}

#: Versions retained per sheet. The 21st evicts the oldest.
MAX_VERSIONS = 20

CONTENT_TYPES = {
    "svg": "image/svg+xml",
    "png": "image/png",
    "pdf": "application/pdf",
    "html": "text/html; charset=utf-8",
    "md": "text/html; charset=utf-8",
}


#: What an origin may look like: a machine name as a tailnet or DNS would
#: spell it. No colon, so `origin:path` is unambiguous; nothing a page would
#: need escaping to show.
_ORIGIN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,253}$")


#: The kinds a browser shows in an iframe. An iframe eats pointer events and
#: has no stable natural size, so these take sheet-level comments only.
FRAME_KINDS = {"html", "md", "pdf"}

#: Only sheet-level comments; see above.
ANCHOR_KINDS = {"svg", "png"}


class PublishError(ValueError):
    """A file was handed to the desk that the desk will not accept."""


class CommentError(ValueError):
    """A comment the desk cannot make, resolve, or remove as asked."""


def sheet_id_for(source_path: str, origin: str | None = None) -> str:
    """Sheet identity: the absolute source path on the machine the file lives on.

    A sheet published here is its path. A sheet sent from another machine is
    its origin plus its path there, spelled `origin:path` the way scp spells
    it, so the same path on two machines is two sheets and a local file at
    that path is a third.
    """
    key = source_path if origin is None else f"{origin}:{source_path}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def check_origin(origin) -> str:
    """Return `origin` if it is a name a sheet can be marked as coming from."""
    if not isinstance(origin, str) or not _ORIGIN.match(origin):
        raise PublishError(
            f"a send needs an origin — the sending machine's name — not {origin!r}"
        )
    return origin


def canonical_source_path(path: Path) -> Path:
    """Spell an absolute path the way the filesystem itself spells it.

    macOS filesystems are case- and unicode-normalisation-insensitive, so
    `fit.svg`, `Fit.SVG` and an NFD-spelled `café.svg` all open one file. Sheet
    identity is the source path, so without this one file becomes two sheets,
    each watching the other's changes and each showing half the history.

    Only a spelling the filesystem has already resolved for us is corrected: a
    name that is on disk exactly as given is kept, which leaves case-sensitive
    filesystems — where those really are different files — completely alone.
    """
    if not os.path.exists(path):
        return path
    walked = Path(path.parts[0])
    for part in path.parts[1:]:
        try:
            entries = os.listdir(walked)
        except OSError:
            return path
        if part not in entries:
            matches = [entry for entry in entries if _same_name(entry, part)]
            if len(matches) != 1:
                return path
            part = matches[0]
        walked = walked / part
    return walked


def _same_name(one: str, other: str) -> bool:
    return (
        unicodedata.normalize("NFC", one).casefold()
        == unicodedata.normalize("NFC", other).casefold()
    )


def judged_parts(path: Path) -> list[str]:
    """The parts of a path the desk gets an opinion about the naming of.

    The file's own name always, plus the directories the user chose to put it
    in. Above the user's home directory — mount points, system temp roots,
    whatever `/private/var/folders` is called this week — the naming is not
    theirs, so the desk does not judge it. For a path outside home that leaves
    the file and the one directory it was written into, which is exactly where
    a half-written `savefig` lands.
    """
    try:
        return list(path.relative_to(Path.home()).parts)
    except (ValueError, OSError, RuntimeError):
        return list(path.parts[-2:])


def check_publishable(path: Path) -> str:
    """Return the sheet kind for `path`, or raise PublishError explaining why not.

    The two rules have deliberately different reach, because they are about
    different things.

    `*_tmp*` is about the *path*. A half-written `savefig` usually lands in a
    scratch directory — `figures_tmp/plot.svg` — and keeping that directory off
    the desk is the whole point of the rule, however the file itself is named.

    A dotfile is about the *file*. Plenty of perfectly good figures live under
    a hidden directory somebody else chose — `~/.claude/`, `~/.cache/`, a tool's
    state directory — and the user did not hide those figures, so the desk does
    not treat them as hidden.
    """
    ext = path.suffix.lower()
    name = path.name
    if name.startswith("."):
        raise PublishError(f"{name}: dotfiles are not published to the desk")
    for part in judged_parts(path):
        if part in (".", ".."):
            continue
        if "_tmp" in part:
            raise PublishError(f"{part}: paths matching *_tmp* are not published to the desk")
    if ext not in ALLOWED_EXTENSIONS:
        allowed = " ".join(sorted(ALLOWED_EXTENSIONS))
        raise PublishError(
            f"{name}: {ext or 'no extension'} is not a desk file type (allowed: {allowed})"
        )
    return ext.lstrip(".")


# -- natural size ---------------------------------------------------------

#: The SVG root tag, wherever it is in the file; attributes are read from it.
_SVG_ROOT = re.compile(rb"<svg\b([^>]*)>", re.IGNORECASE | re.DOTALL)
_SVG_ATTR = re.compile(rb"""([A-Za-z_:][-A-Za-z0-9_:.]*)\s*=\s*("([^"]*)"|'([^']*)')""")
_LENGTH = re.compile(r"^\s*([0-9]*\.?[0-9]+(?:[eE][-+]?[0-9]+)?)\s*(px)?\s*$")

#: How much of a file the SVG root tag is looked for in. It is at the top.
_SVG_HEAD = 256 * 1024


def natural_size(kind: str, data: bytes) -> dict | None:
    """The content's natural box, `{"w", "h"}`, read from the stored bytes.

    This is what a comment's fractions are fractions *of*, and it is computed
    here so that the `desk` command never has to measure anything itself.

    PNG: the IHDR chunk, which every PNG starts with. SVG: `width` and
    `height` when they are plain numbers or in `px`; otherwise the `viewBox`.
    A matplotlib SVG says `width="460.8pt"` and `viewBox="0 0 460.8 345.6"`,
    and the viewBox is the coordinate system the file is actually drawn in —
    the one an agent reading the file will find — so a unit it would have to
    convert is not converted, it is passed over. The frame kinds have no
    natural size: an iframe has nothing stable to measure. None when the
    size cannot be read.
    """
    try:
        if kind == "png":
            return _png_size(data)
        if kind == "svg":
            return _svg_size(data)
    except (ValueError, struct.error, IndexError):
        return None
    return None


def _png_size(data: bytes) -> dict | None:
    if not data.startswith(b"\x89PNG\r\n\x1a\n") or data[12:16] != b"IHDR":
        return None
    w, h = struct.unpack(">II", data[16:24])
    return {"w": w, "h": h} if w > 0 and h > 0 else None


def _svg_size(data: bytes) -> dict | None:
    match = _SVG_ROOT.search(data[:_SVG_HEAD])
    if not match:
        return None
    attrs = {}
    for name, _, double, single in _SVG_ATTR.findall(match.group(1)):
        attrs[name.decode("ascii", "replace").lower()] = (double or single).decode("utf-8", "replace")
    w = _px_length(attrs.get("width"))
    h = _px_length(attrs.get("height"))
    if w and h:
        return {"w": w, "h": h}
    box = (attrs.get("viewbox") or "").replace(",", " ").split()
    if len(box) == 4:
        bw, bh = float(box[2]), float(box[3])
        if math.isfinite(bw) and math.isfinite(bh) and bw > 0 and bh > 0:
            return {"w": _tidy(bw), "h": _tidy(bh)}
    return None


def _px_length(value) -> float | None:
    """A length in CSS pixels, or None if it carries a unit or is missing."""
    if not value:
        return None
    match = _LENGTH.match(value)
    if not match:
        return None
    number = float(match.group(1))
    return _tidy(number) if math.isfinite(number) and number > 0 else None


def _tidy(number: float):
    """`100.0` is reported as `100`; `460.8` stays as it is."""
    return int(number) if number == int(number) else number


# -- comments -------------------------------------------------------------


def check_anchor(anchor) -> dict | None:
    """Return a clean anchor, or raise CommentError.

    An anchor is a rectangle in fractions `0..1` of the content's natural
    box; a point is a rectangle of zero size; `None` is the whole sheet.
    """
    if anchor is None:
        return None
    if not isinstance(anchor, dict):
        raise CommentError(f"an anchor is a rectangle {{x, y, w, h}} in fractions 0..1, not {anchor!r}")
    clean = {}
    for key in ("x", "y", "w", "h"):
        value = anchor.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise CommentError(f"anchor.{key} must be a fraction between 0 and 1, not {value!r}")
        if not 0 <= value <= 1:
            raise CommentError(f"anchor.{key} must be a fraction between 0 and 1, not {value!r}")
        clean[key] = float(value)
    # A rectangle the browser measured can overshoot 1 by a rounding error;
    # one that really leaves the box is refused.
    if clean["x"] + clean["w"] > 1 + 1e-6 or clean["y"] + clean["h"] > 1 + 1e-6:
        raise CommentError("the anchor rectangle leaves the box: x + w and y + h must stay within 1")
    clean["w"] = min(clean["w"], 1 - clean["x"])
    clean["h"] = min(clean["h"], 1 - clean["y"])
    return clean


def check_text(text) -> str:
    if not isinstance(text, str) or not text.strip():
        raise CommentError("a comment needs some text")
    return text.strip()



class Store:
    """Owns sheet records, their versions, their content, and tombstones."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.content_root = self.root / "content"
        self.index_path = self.root / "sheets.json"
        self.content_root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._sizes: dict[tuple[str, int], dict | None] = {}
        self._sheets: dict[str, dict] = self._load()

    # -- persistence ------------------------------------------------------

    def _load(self) -> dict[str, dict]:
        """Read the index back, keeping whatever survived.

        A damaged index must never stop the desk from starting — under launchd
        that reads as "the desk is simply gone" with nothing on screen to say
        why. A record that cannot be read is dropped; the rest still show up.
        """
        if not self.index_path.exists():
            return {}
        try:
            raw = json.loads(self.index_path.read_text())
        except (json.JSONDecodeError, UnicodeDecodeError, OSError, ValueError):
            return {}
        if not isinstance(raw, dict) or not isinstance(raw.get("sheets"), list):
            return {}
        sheets = {}
        for record in raw["sheets"]:
            readable = _readable_sheet(record)
            if readable is not None:
                sheets[readable["id"]] = readable
        return sheets

    def _save(self) -> None:
        payload = {"sheets": list(self._sheets.values())}
        tmp = self.index_path.with_suffix(".json.writing")
        tmp.write_text(json.dumps(payload, indent=2))
        tmp.replace(self.index_path)

    # -- publishing -------------------------------------------------------

    def publish(self, source_path: str | Path) -> dict:
        """Hand a file to the desk. Always appends a version; clears a tombstone.

        Known source path -> new version on the existing sheet.
        Unknown source path -> new sheet.
        """
        return self._ingest(source_path, explicit=True)

    def ingest_change(self, source_path: str | Path) -> dict | None:
        """A watched file changed on disk. Appends a version unless the bytes
        are unchanged, and never resurrects a tombstoned sheet."""
        try:
            return self._ingest(source_path, explicit=False)
        except PublishError:
            return None

    def receive(self, origin, source_path, data: bytes) -> dict:
        """The receiving end of a send: a file's bytes from another machine.

        The path is the sender's, on the sender's disk. It is never resolved
        here — realpath and case-folding against this filesystem would rewrite
        it into a name for some other file — and the sheet it makes is never
        watched, because the file it came from is not on this machine. A send
        is explicit: a person acted, so it clears a tombstone like a publish.
        """
        origin = check_origin(origin)
        if not isinstance(source_path, str) or not source_path.strip():
            raise PublishError(f"a send needs the file's path on {origin}, not {source_path!r}")
        if not isinstance(data, (bytes, bytearray)):
            raise PublishError("a send needs the file's bytes")
        kind = check_publishable(Path(source_path))
        return self._record(source_path, origin, kind, bytes(data), explicit=True)

    def _ingest(self, source_path: str | Path, *, explicit: bool) -> dict | None:
        try:
            path = Path(source_path).expanduser()
            if not path.is_absolute():
                # Resolving this against the server's own working directory
                # would mean the same text named different files to the caller
                # and to the desk — and sheet identity is the absolute source
                # path. Say so instead of guessing.
                raise PublishError(
                    f"{source_path}: publish needs an absolute path "
                    f"(a sheet's identity is where the file is)"
                )
            path = canonical_source_path(Path(os.path.realpath(path)))
        except (OSError, ValueError, TypeError) as exc:
            raise PublishError(f"{_short(source_path)}: unusable path ({exc})") from exc

        kind = check_publishable(path)
        try:
            readable = path.is_file()
        except (OSError, ValueError) as exc:
            raise PublishError(f"{_short(path)}: unusable path ({exc})") from exc
        if not readable:
            raise PublishError(f"{path}: no such file")
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise PublishError(f"{path}: cannot read ({exc.strerror})") from exc

        return self._record(str(path), None, kind, data, explicit=explicit)

    def _record(
        self, source_path: str, origin: str | None, kind: str, data: bytes, *, explicit: bool
    ) -> dict | None:
        """Append a version to the sheet these bytes belong to, creating it if new."""
        sid = sheet_id_for(source_path, origin)
        digest = hashlib.sha256(data).hexdigest()

        with self._lock:
            sheet = self._sheets.get(sid)
            if sheet is None:
                sheet = {
                    "id": sid,
                    "source_path": source_path,
                    "origin": origin,
                    "kind": kind,
                    "versions": [],
                    "comments": [],
                    "trashed": False,
                    "created_at": time.time(),
                    "updated_at": time.time(),
                }
                self._sheets[sid] = sheet
            else:
                if sheet["trashed"]:
                    if not explicit:
                        # Tombstoned: a file change must never resurrect a sheet.
                        return None
                    sheet["trashed"] = False
                if not explicit and sheet["versions"] and sheet["versions"][-1].get("sha256") == digest:
                    return None

            sheet["kind"] = kind
            n = (sheet["versions"][-1]["n"] + 1) if sheet["versions"] else 1
            stored = f"{n}.{kind}"
            dest_dir = self.content_root / sid
            dest_dir.mkdir(parents=True, exist_ok=True)
            (dest_dir / stored).write_bytes(data)
            sheet["versions"].append(
                {
                    "n": n,
                    "file": stored,
                    "sha256": digest,
                    "bytes": len(data),
                    "created_at": time.time(),
                }
            )
            self._evict(sheet)
            sheet["updated_at"] = time.time()
            self._save()
            return dict(sheet)

    def _evict(self, sheet: dict) -> None:
        while len(sheet["versions"]) > MAX_VERSIONS:
            oldest = sheet["versions"].pop(0)
            stale = self.content_root / sheet["id"] / oldest["file"]
            stale.unlink(missing_ok=True)
            self._sizes.pop((sheet["id"], oldest["n"]), None)

    # -- reading ----------------------------------------------------------

    def get(self, sheet_id: str) -> dict | None:
        with self._lock:
            sheet = self._sheets.get(sheet_id)
            return dict(sheet) if sheet else None

    def live_sheets(self) -> list[dict]:
        with self._lock:
            return [dict(s) for s in self._sheets.values() if not s["trashed"]]

    def trashed_sheets(self) -> list[dict]:
        with self._lock:
            return [dict(s) for s in self._sheets.values() if s["trashed"]]

    def watched_paths(self) -> list[str]:
        """Watching is implicit: every live sheet's source path, and nothing else.

        A sheet with an origin is left out. Its path names a file on another
        machine, and a file that happens to sit at the same path here is a
        different file — one whose changes must not become that sheet's
        versions.
        """
        with self._lock:
            return [
                s["source_path"]
                for s in self._sheets.values()
                if not s["trashed"] and not s.get("origin")
            ]

    def content(self, sheet_id: str, version: int | None = None) -> tuple[bytes, str]:
        with self._lock:
            sheet = self._sheets.get(sheet_id)
            if sheet is None or not sheet["versions"]:
                raise KeyError(sheet_id)
            if version is None:
                record = sheet["versions"][-1]
            else:
                match = [v for v in sheet["versions"] if v["n"] == version]
                if not match:
                    raise KeyError((sheet_id, version))
                record = match[0]
            kind = sheet["kind"]
            path = self.content_root / sheet_id / record["file"]
        return path.read_bytes(), CONTENT_TYPES.get(kind, "application/octet-stream")

    def natural_size(self, sheet_id: str, version: int | None = None) -> dict | None:
        """The natural box of one version's content, or None if it has none
        or it cannot be read. Cached: the bytes of a version never change."""
        with self._lock:
            sheet = self._sheets.get(sheet_id)
            if sheet is None or not sheet["versions"]:
                return None
            if version is None:
                record = sheet["versions"][-1]
            else:
                match = [v for v in sheet["versions"] if v["n"] == version]
                if not match:
                    return None
                record = match[0]
            key = (sheet_id, record["n"])
            if key in self._sizes:
                return dict(self._sizes[key]) if self._sizes[key] else None
            if sheet["kind"] not in ANCHOR_KINDS:
                size = None
            else:
                try:
                    data = (self.content_root / sheet_id / record["file"]).read_bytes()
                except OSError:
                    return None
                size = natural_size(sheet["kind"], data)
            self._sizes[key] = size
            return dict(size) if size else None

    # -- comments ---------------------------------------------------------

    def add_comment(self, sheet_id: str, anchor, text) -> dict:
        """Pin a comment on a sheet, stamped with the sheet's current version."""
        text = check_text(text)
        anchor = check_anchor(anchor)
        with self._lock:
            sheet = self._live(sheet_id)
            if anchor is not None and sheet["kind"] not in ANCHOR_KINDS:
                raise CommentError(
                    f"a .{sheet['kind']} sheet takes sheet-level comments only "
                    f"(no anchor): an iframe has no stable natural size"
                )
            taken = {c["id"] for c in sheet["comments"]}
            cid = secrets.token_hex(4)
            while cid in taken:
                cid = secrets.token_hex(4)
            sheet["comments"].append(
                {
                    "id": cid,
                    "version": sheet["versions"][-1]["n"],
                    "anchor": anchor,
                    "text": text,
                    "created_at": time.time(),
                    "resolved_at": None,
                }
            )
            sheet["updated_at"] = time.time()
            self._save()
            return dict(sheet)

    def resolve_comment(self, sheet_id: str, comment_id) -> dict:
        with self._lock:
            sheet = self._live(sheet_id)
            comment = self._comment(sheet, comment_id)
            if comment["resolved_at"] is None:
                comment["resolved_at"] = time.time()
                sheet["updated_at"] = time.time()
                self._save()
            return dict(sheet)

    def remove_comment(self, sheet_id: str, comment_id) -> dict:
        with self._lock:
            sheet = self._live(sheet_id)
            comment = self._comment(sheet, comment_id)
            sheet["comments"].remove(comment)
            sheet["updated_at"] = time.time()
            self._save()
            return dict(sheet)

    def _live(self, sheet_id) -> dict:
        sheet = self._sheets.get(sheet_id) if isinstance(sheet_id, str) else None
        if sheet is None:
            raise CommentError(f"no sheet {sheet_id!r} on this desk")
        if sheet["trashed"]:
            raise CommentError(f"sheet {sheet_id!r} is in the trash; restore it first")
        return sheet

    @staticmethod
    def _comment(sheet: dict, comment_id) -> dict:
        for comment in sheet["comments"]:
            if comment["id"] == comment_id:
                return comment
        raise CommentError(f"no comment {comment_id!r} on sheet {sheet['id']!r}")

    # -- trash and tombstones ---------------------------------------------

    def trash(self, sheet_id: str) -> dict | None:
        """Tombstone a source path: it stops being watched and a later file
        change will not bring it back."""
        with self._lock:
            sheet = self._sheets.get(sheet_id)
            if sheet is None:
                return None
            sheet["trashed"] = True
            sheet["updated_at"] = time.time()
            self._save()
            return dict(sheet)

    def restore(self, sheet_id: str) -> dict | None:
        with self._lock:
            sheet = self._sheets.get(sheet_id)
            if sheet is None:
                return None
            sheet["trashed"] = False
            sheet["updated_at"] = time.time()
            self._save()
            return dict(sheet)


def _short(value, limit: int = 120) -> str:
    """A path short enough to put in an error message."""
    text = str(value)
    return text if len(text) <= limit else text[:limit] + "…"


def _readable_sheet(record) -> dict | None:
    """One sheet record from the index, or None if too little of it survived."""
    if not isinstance(record, dict):
        return None
    if not isinstance(record.get("id"), str) or not isinstance(record.get("source_path"), str):
        return None
    if not isinstance(record.get("kind"), str) or not isinstance(record.get("versions"), list):
        return None
    versions = [
        v
        for v in record["versions"]
        if isinstance(v, dict)
        and isinstance(v.get("n"), int)
        and not isinstance(v.get("n"), bool)
        and isinstance(v.get("file"), str)
    ]
    if not versions:
        return None
    # Anything else on the record is left alone, so that a sidecar added later
    # survives a round trip through a version of the desk that predates it.
    record = dict(record)
    record["versions"] = versions
    comments = record.get("comments")
    record["comments"] = [
        c for c in (_readable_comment(c) for c in (comments if isinstance(comments, list) else []))
        if c is not None
    ]
    record["trashed"] = bool(record.get("trashed"))
    origin = record.get("origin")
    record["origin"] = origin if isinstance(origin, str) and _ORIGIN.match(origin) else None
    for stamp in ("created_at", "updated_at"):
        value = record.get(stamp)
        ok = isinstance(value, (int, float)) and not isinstance(value, bool)
        record[stamp] = float(value) if ok else 0.0
    return record


def _readable_comment(record) -> dict | None:
    """One comment off the index, or None if too little of it survived.

    A damaged comment is dropped the way a damaged version is: the sheet
    still starts with the rest."""
    if not isinstance(record, dict):
        return None
    if not isinstance(record.get("id"), str) or not record["id"]:
        return None
    version = record.get("version")
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        return None
    if not isinstance(record.get("text"), str) or not record["text"].strip():
        return None
    try:
        anchor = check_anchor(record.get("anchor"))
    except CommentError:
        return None
    resolved = record.get("resolved_at")
    if resolved is not None and (isinstance(resolved, bool) or not isinstance(resolved, (int, float))):
        return None
    created = record.get("created_at")
    ok = isinstance(created, (int, float)) and not isinstance(created, bool)
    return {
        "id": record["id"],
        "version": version,
        "anchor": anchor,
        "text": record["text"],
        "created_at": float(created) if ok else 0.0,
        "resolved_at": float(resolved) if resolved is not None else None,
    }

"""The store — the desk's own copy of every published file.

Publishing copies the file in and appends a version; it never references the
source in place, so deleting or clobbering the source cannot damage a sheet.

Sheet identity is the absolute source path. Same path means same sheet,
forever. This one rule is what makes update-in-place the default.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
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


class PublishError(ValueError):
    """A file was handed to the desk that the desk will not accept."""


def sheet_id_for(source_path: str) -> str:
    """Sheet identity, derived from the absolute source path and nothing else."""
    return hashlib.sha256(source_path.encode("utf-8")).hexdigest()[:16]


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


class Store:
    """Owns sheet records, their versions, their content, and tombstones."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.content_root = self.root / "content"
        self.index_path = self.root / "sheets.json"
        self.content_root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
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

        sid = sheet_id_for(str(path))
        digest = hashlib.sha256(data).hexdigest()

        with self._lock:
            sheet = self._sheets.get(sid)
            if sheet is None:
                sheet = {
                    "id": sid,
                    "source_path": str(path),
                    "kind": kind,
                    "versions": [],
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
        """Watching is implicit: every live sheet's source path, and nothing else."""
        with self._lock:
            return [s["source_path"] for s in self._sheets.values() if not s["trashed"]]

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

    def forget(self, sheet_id: str) -> None:
        """Drop a sheet and its content entirely. Emptying the trash."""
        with self._lock:
            if self._sheets.pop(sheet_id, None) is None:
                return
            shutil.rmtree(self.content_root / sheet_id, ignore_errors=True)
            self._save()


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
    record["trashed"] = bool(record.get("trashed"))
    for stamp in ("created_at", "updated_at"):
        value = record.get(stamp)
        ok = isinstance(value, (int, float)) and not isinstance(value, bool)
        record[stamp] = float(value) if ok else 0.0
    record.pop("created", None)  # a transient flag that older desks leaked onto disk
    return record

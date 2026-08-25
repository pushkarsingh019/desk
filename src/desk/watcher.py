"""The watcher — observes source paths that have been published at least once.

Watching is implicit. There is no watch registry and no configured directory:
the set of watched paths is exactly the set of live sheets' source paths, which
the store hands over on every poll. A file that has never been published cannot
be picked up, no matter where it is written.

Changes are debounced, so a half-written `savefig` is never ingested mid-write.
"""

from __future__ import annotations

import os
import threading
import time

DEFAULT_POLL_INTERVAL = 0.1
DEFAULT_DEBOUNCE = 0.3


def _stamp(path: str):
    """A cheap fingerprint of a file: (mtime, size, inode). None if it isn't there.

    The inode is what catches a save-by-rename. `savefig`, editors, and `cp -p`
    all write a new file and rename it over the old one, and the replacement
    can arrive with the mtime and size of the file it replaced — invisible to a
    fingerprint made of those two alone. A new inode is not.
    """
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size, st.st_ino)


class Watcher:
    def __init__(
        self,
        watched_paths,
        on_change,
        poll_interval: float = DEFAULT_POLL_INTERVAL,
        debounce: float = DEFAULT_DEBOUNCE,
    ):
        self._watched_paths = watched_paths
        self._on_change = on_change
        self._poll_interval = poll_interval
        self._debounce = debounce
        self._seen: dict[str, tuple | None] = {}
        self._pending: dict[str, float] = {}
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="desk-watcher", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.poll_once()
            except Exception:  # a watcher must never take the server down
                pass
            self._stop.wait(self._poll_interval)

    def poll_once(self) -> None:
        now = time.monotonic()
        paths = set(self._watched_paths())

        # A path that stopped being watched (trashed) drops out entirely, along
        # with any change of its that was still settling.
        for gone in set(self._seen) - paths:
            self._seen.pop(gone, None)
            self._pending.pop(gone, None)

        for path in paths:
            stamp = _stamp(path)
            if path not in self._seen:
                # First sight of a freshly published path. Treat it as a change
                # rather than a silent baseline: the file may have been rewritten
                # in the instant between the publish and this poll, and a change
                # that turns out to be nothing costs one hash the store discards.
                self._seen[path] = stamp
                if stamp is not None:
                    self._pending[path] = now
                continue
            if stamp is None:
                # The source was deleted. The sheet keeps its stored copy; we
                # simply have nothing new to ingest.
                self._seen[path] = None
                self._pending.pop(path, None)
                continue
            if stamp != self._seen[path]:
                self._seen[path] = stamp
                self._pending[path] = now

        for path, changed_at in list(self._pending.items()):
            if now - changed_at >= self._debounce:
                del self._pending[path]
                self._on_change(path)

"""Test harness for seam 1: the HTTP API.

Every test in `test_api.py` drives a real server process through HTTP and
nothing else. Nothing here reaches inside the server.
"""

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Response:
    def __init__(self, status: int, headers: dict, body: bytes):
        self.status = status
        self.headers = headers
        self.body = body

    @property
    def text(self) -> str:
        return self.body.decode("utf-8")

    def json(self):
        return json.loads(self.body)


class DeskServer:
    """A desk server in its own process, reachable only over HTTP."""

    def __init__(self, data_dir: Path, env: dict):
        self.data_dir = data_dir
        self.port = _free_port()
        self._env = env
        self.proc: subprocess.Popen | None = None
        self.start()

    # -- process lifetime -------------------------------------------------
    def start(self) -> None:
        env = dict(os.environ)
        env.update(
            {
                "DESK_PORT": str(self.port),
                "DESK_HOST": "127.0.0.1",
                "DESK_DATA_DIR": str(self.data_dir),
                "PYTHONUNBUFFERED": "1",
            }
        )
        env.update(self._env)
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "desk.server"],
            cwd=REPO,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        self._await_port()

    def _await_port(self, timeout: float = 20.0) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.proc.poll() is not None:
                out = self.proc.stdout.read().decode("utf-8", "replace")
                raise RuntimeError(f"desk server exited early:\n{out}")
            try:
                with socket.create_connection(("127.0.0.1", self.port), 0.2):
                    return
            except OSError:
                time.sleep(0.05)
        raise RuntimeError("desk server never opened its port")

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=5)

    def restart(self) -> None:
        self.stop()
        self.start()

    # -- HTTP -------------------------------------------------------------
    def request(self, method: str, path: str, body=None, timeout: float = 10.0) -> Response:
        import http.client

        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=timeout)
        try:
            headers = {}
            payload = None
            if body is not None:
                payload = json.dumps(body).encode("utf-8")
                headers["Content-Type"] = "application/json"
                headers["Content-Length"] = str(len(payload))
            conn.request(method, path, body=payload, headers=headers)
            resp = conn.getresponse()
            return Response(resp.status, dict(resp.getheaders()), resp.read())
        finally:
            conn.close()

    def get(self, path: str, **kw) -> Response:
        return self.request("GET", path, **kw)

    def post(self, path: str, body, **kw) -> Response:
        return self.request("POST", path, body=body, **kw)

    # -- desk vocabulary --------------------------------------------------
    def publish(self, source_path) -> Response:
        return self.post("/api/publish", {"source_path": str(source_path)})

    def state(self) -> dict:
        resp = self.get("/api/state")
        assert resp.status == 200, resp.text
        return resp.json()

    def sheets(self) -> list:
        return self.state()["sheets"]

    def sheet_for(self, source_path) -> dict | None:
        want = str(Path(source_path).resolve())
        for sheet in self.sheets():
            if sheet["source_path"] == want:
                return sheet
        return None

    def await_condition(self, predicate, timeout: float = 8.0, what: str = "condition"):
        """Poll the API until `predicate(state)` returns truthy."""
        deadline = time.time() + timeout
        last = None
        while time.time() < deadline:
            last = self.state()
            result = predicate(last)
            if result:
                return result
            time.sleep(0.05)
        raise AssertionError(f"timed out waiting for {what}; last state: {json.dumps(last)[:2000]}")

    def events(self, timeout: float = 10.0):
        """Open the SSE stream. Returns an EventStream to read from."""
        return EventStream(self.port, timeout)


class EventStream:
    """A live SSE subscription, read as decoded JSON events."""

    def __init__(self, port: int, timeout: float):
        import http.client

        self._conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
        self._conn.request("GET", "/api/events", headers={"Accept": "text/event-stream"})
        self._resp = self._conn.getresponse()
        assert self._resp.status == 200, self._resp.status
        self.content_type = self._resp.getheader("Content-Type")
        self._buf = b""

    def next_event(self, timeout: float = 8.0) -> dict:
        deadline = time.time() + timeout
        while time.time() < deadline:
            while b"\n\n" in self._buf:
                raw, self._buf = self._buf.split(b"\n\n", 1)
                event = _parse_sse(raw)
                if event is not None:
                    return event
            chunk = self._resp.read(1)
            if not chunk:
                raise AssertionError("SSE stream closed")
            self._buf += chunk
        raise AssertionError("timed out waiting for an SSE event")

    def await_event(self, kind: str, timeout: float = 8.0) -> dict:
        deadline = time.time() + timeout
        seen = []
        while time.time() < deadline:
            event = self.next_event(timeout=max(0.1, deadline - time.time()))
            if event.get("type") == kind:
                return event
            seen.append(event.get("type"))
        raise AssertionError(f"never saw {kind!r}; saw {seen}")

    def drain(self, seconds: float) -> list:
        """Collect every event that arrives in the next `seconds`."""
        events = []
        deadline = time.time() + seconds
        while time.time() < deadline:
            try:
                events.append(self.next_event(timeout=max(0.05, deadline - time.time())))
            except AssertionError:
                break
        return events

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def _parse_sse(raw: bytes) -> dict | None:
    data_lines = []
    for line in raw.decode("utf-8").splitlines():
        if line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
    if not data_lines:
        return None
    return json.loads("\n".join(data_lines))


@pytest.fixture
def desk(tmp_path):
    server = DeskServer(tmp_path / "data", env={})
    try:
        yield server
    finally:
        server.stop()


@pytest.fixture
def figures(tmp_path):
    """A directory to write source figures into, standing in for a project dir."""
    d = tmp_path / "figures"
    d.mkdir()
    return d


SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="60">'
    '<rect width="100" height="60" fill="{color}"/></svg>'
)

# A 1x1 PNG.
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d494844520000000100000001080600000"
    "01f15c4890000000d49444154789c6360000002000100ffff03000006"
    "000557bfabd40000000049454e44ae426082"
)


def run_desk(*args, cwd=None, port=None, data_dir=None, env=None, timeout=60):
    """Run the `desk` command the way the /desk skill runs it."""
    environ = dict(os.environ)
    environ.update(
        {
            "DESK_HOST": "127.0.0.1",
            "DESK_PORT": str(port) if port else "7777",
            "PYTHONUNBUFFERED": "1",
        }
    )
    if data_dir:
        environ["DESK_DATA_DIR"] = str(data_dir)
    if cwd:
        environ["DESK_CWD"] = str(cwd)
    environ.update(env or {})
    proc = subprocess.run(
        [sys.executable, "-m", "desk.cli", *args],
        cwd=str(cwd or REPO),
        env=environ,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return proc


@pytest.fixture
def free_port():
    """A port to start a desk on, cleaned up however the test leaves it."""
    port = _free_port()
    yield port
    try:
        pids = subprocess.run(
            ["lsof", "-ti", f"tcp:{port}"], capture_output=True, text=True
        ).stdout.split()
        for pid in pids:
            subprocess.run(["kill", pid])
    except Exception:
        pass

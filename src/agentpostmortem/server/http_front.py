"""A plain HTTP front for a :class:`Session`, standard library only.

Endpoints, all JSON:

* ``GET /tools``: scenario id, instructions and the tool list with input schemas.
* ``POST /tools/{name}``: call a tool with a JSON object of arguments.
* ``POST /finish``: ``{"status": "completed"|"failed", "message": "..."}``.
* ``GET /results``: the ``results.json`` document for the run so far.

A tool call answers ``{"ok": true, "step": ..., "result": {...}}`` or
``{"ok": false, "step": ..., "error": "timeout", "message": ...}``; the HTTP status
mirrors the simulated failure (504 timeout, 500 server error, 429 rate limit).

The server binds to 127.0.0.1 only and rejects requests whose ``Host`` or
``Origin`` is not loopback, so a web page cannot drive it from a browser.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any
from urllib.parse import urlsplit

from agentpostmortem.server.session import FINISH_TOOL, CallResult, Session, SessionError

LOOPBACK = "127.0.0.1"
MAX_BODY = 1_000_000
_LOCAL_HOSTS = {LOOPBACK, "localhost", "[::1]"}

_ERROR_STATUS = {
    "timeout": HTTPStatus.GATEWAY_TIMEOUT,
    "server_error": HTTPStatus.INTERNAL_SERVER_ERROR,
    "rate_limit": HTTPStatus.TOO_MANY_REQUESTS,
    "not_found": HTTPStatus.NOT_FOUND,
    "invalid_request": HTTPStatus.BAD_REQUEST,
    "conflict": HTTPStatus.CONFLICT,
}
_SESSION_ERROR_STATUS = {
    "unknown_tool": HTTPStatus.NOT_FOUND,
    "bad_arguments": HTTPStatus.BAD_REQUEST,
    "finished": HTTPStatus.CONFLICT,
}


class SessionHTTPServer(HTTPServer):
    """An HTTP server bound to loopback that serves one session."""

    def __init__(
        self,
        session: Session,
        port: int = 0,
        on_call: Callable[[], None] | None = None,
    ) -> None:
        self.session = session
        self.on_call = on_call
        super().__init__((LOOPBACK, port), _Handler)

    @property
    def port(self) -> int:
        return int(self.server_address[1])

    @property
    def url(self) -> str:
        return f"http://{LOOPBACK}:{self.port}"


def _host_is_local(value: str | None) -> bool:
    if not value:
        return True
    host = urlsplit(f"//{value}").hostname or ""
    return host in _LOCAL_HOSTS or f"[{host}]" in _LOCAL_HOSTS


def _origin_is_local(value: str | None) -> bool:
    if not value:
        return True
    parts = urlsplit(value)
    return parts.scheme in {"http", "https"} and _host_is_local(parts.netloc)


class _Handler(BaseHTTPRequestHandler):
    server: SessionHTTPServer
    server_version = "agentpostmortem"

    def log_message(self, format: str, *args: Any) -> None:
        """Keep stderr quiet; the ledger is the log that matters."""

    def _send(self, status: int, body: object) -> None:
        data = (json.dumps(body, indent=2) + "\n").encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _guard(self) -> bool:
        if not _host_is_local(self.headers.get("Host")) or not _origin_is_local(
            self.headers.get("Origin")
        ):
            self._send(HTTPStatus.FORBIDDEN, {"error": "forbidden", "message": "loopback only"})
            return False
        return True

    def _body(self) -> dict[str, Any] | None:
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            self._send(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "too_large"})
            return None
        raw = self.rfile.read(length) if length else b""
        if not raw.strip():
            return {}
        try:
            value = json.loads(raw)
        except ValueError:
            self._send(HTTPStatus.BAD_REQUEST, {"error": "bad_json", "message": "invalid JSON"})
            return None
        if not isinstance(value, dict):
            self._send(HTTPStatus.BAD_REQUEST, {"error": "bad_json", "message": "send an object"})
            return None
        return value

    def do_GET(self) -> None:
        if not self._guard():
            return
        session = self.server.session
        path = urlsplit(self.path).path.rstrip("/")
        if path == "/tools":
            self._send(
                HTTPStatus.OK,
                {
                    "scenario": session.scenario.id,
                    "instructions": session.instructions,
                    "tools": [tool.to_dict() for tool in session.list_tools()],
                },
            )
        elif path == "/results":
            self._send(HTTPStatus.OK, session.results_document())
        else:
            self._send(HTTPStatus.NOT_FOUND, {"error": "not_found", "message": self.path})

    def do_POST(self) -> None:
        if not self._guard():
            return
        path = urlsplit(self.path).path.rstrip("/")
        if path == "/finish":
            name = FINISH_TOOL
        elif path.startswith("/tools/") and path.count("/") == 2:
            name = path.removeprefix("/tools/")
        else:
            self._send(HTTPStatus.NOT_FOUND, {"error": "not_found", "message": self.path})
            return
        body = self._body()
        if body is None:
            return
        try:
            result = self.server.session.call(name, body)
        except SessionError as error:
            status = _SESSION_ERROR_STATUS.get(error.code, HTTPStatus.BAD_REQUEST)
            self._send(status, {"ok": False, "error": error.code, "message": error.message})
            return
        self._send(*_response(result))
        if self.server.on_call is not None:
            self.server.on_call()


def _response(result: CallResult) -> tuple[int, dict[str, Any]]:
    if result.ok:
        return HTTPStatus.OK, {"ok": True, "step": result.step, "result": result.body}
    status = _ERROR_STATUS.get(str(result.body.get("error")), HTTPStatus.BAD_GATEWAY)
    return status, {"ok": False, "step": result.step, **result.body}


def start_in_thread(server: SessionHTTPServer) -> threading.Thread:
    """Serve in a daemon thread; stop with ``server.shutdown()``."""
    thread = threading.Thread(target=server.serve_forever, name="apm-http", daemon=True)
    thread.start()
    return thread

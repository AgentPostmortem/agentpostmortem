"""Hermes plugin that exposes an AgentPostmortem scenario's services as tools.

The AgentPostmortem Hermes adapter installs this plugin into a throwaway
``HERMES_HOME`` for each run and enables it in that home's ``config.yaml``.
It registers one tool per service operation of the scenario plan with the
documented plugin API (``ctx.register_tool``). Each handler forwards its call to
the benchmark process on 127.0.0.1, which runs it against the simulated service.

A successful call returns the service result as JSON. A failed call raises an
exception named after the simulated error (for example ``ToolTimeout``), so
Hermes handles it exactly as it handles any tool that raises. The plugin adds no
retries, no idempotency keys and no judgement of its own.

Configuration comes from the environment the adapter sets:

* ``APM_HERMES_TOOLS``: path of a JSON file listing the tool schemas.
* ``APM_HERMES_ENDPOINT``: ``host:port`` of the benchmark process.

Hermes passes a tool handler its arguments but not the id of the tool call the
model made. The ``pre_tool_call`` hook, which Hermes fires once right before a
tool runs and which does carry ``tool_call_id``, records that id so the
benchmark can tie the call to its plan step.
"""

from __future__ import annotations

import http.client
import json
import os
from collections.abc import Callable
from typing import Any

TOOLSET = "agentpostmortem"

_current_call: dict[str, str] = {}
_error_types: dict[str, type[Exception]] = {}


def _error_type(name: str) -> type[Exception]:
    """An exception class carrying the simulated error's name, as a real client would raise."""
    if name not in _error_types:
        _error_types[name] = type(name, (Exception,), {"__module__": "agentpostmortem.sims"})
    return _error_types[name]


def _post(path: str, payload: dict[str, Any]) -> dict[str, Any]:
    host, _, port = os.environ["APM_HERMES_ENDPOINT"].rpartition(":")
    connection = http.client.HTTPConnection(host, int(port), timeout=60)
    try:
        body = json.dumps(payload).encode()
        connection.request("POST", path, body, {"Content-Type": "application/json"})
        response = connection.getresponse()
        data: dict[str, Any] = json.loads(response.read() or b"{}")
    finally:
        connection.close()
    return data


def _record_call(tool_name: str = "", tool_call_id: str = "", **kwargs: Any) -> None:
    _current_call["id"] = str(tool_call_id or "")
    _current_call["name"] = str(tool_name or "")


def _handler(name: str) -> Callable[..., str]:
    def handle(params: dict[str, Any], **kwargs: Any) -> str:
        reply = _post(
            "/apm/tool",
            {"name": name, "call_id": _current_call.get("id", ""), "arguments": params},
        )
        if "result" in reply:
            return str(reply["result"])
        raise _error_type(str(reply.get("error_type", "ToolError")))(str(reply.get("message", "")))

    handle.__name__ = name
    return handle


def register(ctx: Any) -> None:
    path = os.environ.get("APM_HERMES_TOOLS")
    if not path:
        return
    with open(path, encoding="utf-8") as handle:
        schemas: list[dict[str, Any]] = json.load(handle)
    ctx.register_hook("pre_tool_call", _record_call)
    for schema in schemas:
        ctx.register_tool(
            name=schema["name"], toolset=TOOLSET, schema=schema, handler=_handler(schema["name"])
        )

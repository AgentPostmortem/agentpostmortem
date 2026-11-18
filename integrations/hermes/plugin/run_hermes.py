"""Run one Hermes Agent conversation for the AgentPostmortem Hermes adapter.

The adapter starts this script with the Python of a Hermes source checkout, as
the Hermes docs describe for embedding (``from run_agent import AIAgent``). It
builds an ``AIAgent`` with default settings, pointed at the custom
OpenAI-compatible endpoint the adapter serves on 127.0.0.1, runs the scenario
task with ``run_conversation`` and writes the result as JSON.

Usage: python -I run_hermes.py HERMES_SRC ENDPOINT_URL TASK RESULT_PATH

Only loopback connections are allowed: any other connection or name lookup is
refused and recorded in the result, so a run can never reach the network and a
test can check that Hermes did not try.
"""

from __future__ import annotations

import importlib
import json
import os
import socket
import sys
from typing import Any

LOOPBACK = {"127.0.0.1", "::1", "localhost"}
TOOLSET = "agentpostmortem"

blocked: list[str] = []


def _guard_network() -> None:
    original_connect = socket.socket.connect
    original_getaddrinfo = socket.getaddrinfo

    def connect(self: socket.socket, address: Any) -> None:
        host = address[0] if isinstance(address, tuple) else None
        if self.family in (socket.AF_INET, socket.AF_INET6) and host not in LOOPBACK:
            blocked.append(f"connect {address!r}")
            raise OSError("network access is disabled in AgentPostmortem framework mode")
        original_connect(self, address)

    def getaddrinfo(host: Any, *args: Any, **kwargs: Any) -> Any:
        if host is not None and str(host) not in LOOPBACK:
            blocked.append(f"lookup {host!r}")
            raise socket.gaierror("network access is disabled in AgentPostmortem framework mode")
        return original_getaddrinfo(host, *args, **kwargs)

    socket.socket.connect = connect  # type: ignore[method-assign,assignment]
    socket.getaddrinfo = getaddrinfo


def main(hermes_src: str, endpoint: str, task: str, result_path: str) -> None:
    _guard_network()
    sys.path.insert(0, hermes_src)
    ai_agent = importlib.import_module("run_agent").AIAgent
    agent = ai_agent(
        base_url=endpoint,
        api_key="agentpostmortem-local",
        provider="custom",
        model="agentpostmortem-scripted",
        quiet_mode=True,
        enabled_toolsets=[TOOLSET],
        skip_context_files=True,
        skip_memory=True,
        skip_background_review=True,
    )
    result: dict[str, Any] = agent.run_conversation(task, task_id="agentpostmortem")
    payload = {
        "final_response": result.get("final_response"),
        "completed": result.get("completed"),
        "failed": result.get("failed"),
        "error": result.get("error"),
        "messages": result.get("messages", []),
        "blocked_network": blocked,
    }
    with open(result_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, default=str)
    close = getattr(agent, "close", None)
    if callable(close):
        close()


if __name__ == "__main__":
    main(*sys.argv[1:5])
    sys.stdout.flush()
    # Skip interpreter teardown: Hermes background threads have nothing left to do.
    os._exit(0)

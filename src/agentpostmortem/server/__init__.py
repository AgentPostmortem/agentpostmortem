"""Serve a scenario to agents in other processes, over MCP or plain HTTP.

The MCP transport needs the optional ``mcp`` extra and is imported lazily from
:mod:`agentpostmortem.server.mcp_server`.
"""

from agentpostmortem.server.session import (
    CRASH_NOT_SUPPORTED,
    FINISH_TOOL,
    NO_FINISH,
    WAIT_TOOL,
    CallResult,
    ExternalRunResult,
    ReportWriter,
    Session,
    SessionError,
    Tool,
)

__all__ = [
    "CRASH_NOT_SUPPORTED",
    "FINISH_TOOL",
    "NO_FINISH",
    "WAIT_TOOL",
    "CallResult",
    "ExternalRunResult",
    "ReportWriter",
    "Session",
    "SessionError",
    "Tool",
]

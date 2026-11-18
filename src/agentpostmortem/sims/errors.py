"""Errors a tool call can surface to an agent. They mirror what real clients raise."""

from __future__ import annotations


class ToolError(Exception):
    """Base class for every failure a simulated service surfaces."""

    code = "error"


class ToolTimeout(ToolError):
    """No response arrived in time. The caller cannot tell whether work happened."""

    code = "timeout"


class ServerError(ToolError):
    code = "server_error"


class RateLimited(ToolError):
    code = "rate_limit"

    def __init__(self, retry_after: int) -> None:
        super().__init__(f"rate limited, retry after {retry_after} ticks")
        self.retry_after = retry_after


class NotFound(ToolError):
    code = "not_found"


class InvalidRequest(ToolError):
    code = "invalid_request"


class Conflict(ToolError):
    """A conditional write was rejected because the state changed underneath it."""

    code = "conflict"


class AgentCrashed(BaseException):
    """Raised by the harness to simulate the agent process dying.

    It derives from BaseException so that agent code catching ``Exception``
    cannot swallow it, just as code cannot catch a real process kill.
    """

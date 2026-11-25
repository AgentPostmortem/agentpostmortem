"""Fault kinds and the triggers that decide when a fault fires."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class FaultKind(StrEnum):
    """What goes wrong inside a simulated service call."""

    TIMEOUT = "timeout"
    """The call times out and nothing happened on the service side."""

    TIMEOUT_AFTER_SUCCESS = "timeout_after_success"
    """The service applied the operation, but the response was lost."""

    SERVER_ERROR = "server_error"
    """The service answers with a server error and nothing happened."""

    RATE_LIMIT = "rate_limit"
    """The service rejects the call with a retry-after hint."""

    MALFORMED = "malformed"
    """The response arrives but is missing fields or has the wrong types."""

    STALE = "stale"
    """A read returns an older, well formed value."""

    LYING = "lying"
    """A fast, well formed response that is wrong."""

    PARTIAL = "partial"
    """A batch call succeeds as a whole but some items in it fail."""

    LATE_COMMIT = "late_commit"
    """The call times out before commit, then commits asynchronously later."""

    SILENT_ERROR = "silent_error"
    """The service returns without raising, carrying an error status in the body."""


class Trigger(BaseModel):
    """When a fault fires, counted per service operation (1-based)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    calls: tuple[int, ...] = Field(default=(1,), description="Call numbers that fault.")
    always: bool = Field(default=False, description="Fault on every call.")

    @model_validator(mode="after")
    def _check(self) -> Trigger:
        if any(n < 1 for n in self.calls):
            raise ValueError("call numbers start at 1")
        return self

    def fires(self, call_number: int) -> bool:
        return self.always or call_number in self.calls


class FaultSpec(BaseModel):
    """A fault injected into one operation of one simulated service."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    service: str
    operation: str
    kind: FaultKind
    trigger: Trigger = Field(default_factory=Trigger)
    params: dict[str, Any] = Field(default_factory=dict)


class CrashSpec(BaseModel):
    """The agent process dies after completing a given plan step (1-based)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    after_step: int = Field(ge=1)
    times: int = Field(default=1, ge=1, description="How many restarts are forced.")

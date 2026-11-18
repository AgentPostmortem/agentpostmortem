"""What an agent reports back, and what the oracles conclude."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class Status(StrEnum):
    COMPLETED = "completed"
    """The agent says the task is done."""
    FAILED = "failed"
    """The agent says it could not finish the task."""


@dataclass(frozen=True, slots=True)
class AgentOutcome:
    status: Status
    claim: str
    """The agent's final message to the user."""
    completed_steps: tuple[str, ...] = ()
    """Step ids the agent believes it completed."""


@dataclass(frozen=True, slots=True)
class ToolCall:
    seq: int
    step: str
    service: str
    operation: str
    attempt: int
    result: str
    """'ok' or the fault/error name that the agent saw."""
    tick: int = 0


@dataclass(frozen=True, slots=True)
class Verdict:
    oracle: str
    passed: bool
    message: str
    evidence: tuple[str, ...] = field(default_factory=tuple)

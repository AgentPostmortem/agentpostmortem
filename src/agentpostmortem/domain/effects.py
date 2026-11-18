"""Side effects recorded by the ledger."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class Effect:
    """One real-world effect a simulated service applied, or refused as a duplicate.

    ``applied`` is False when the service recognised an idempotency key it had
    already applied and returned the original result instead of acting again.
    """

    seq: int
    service: str
    operation: str
    subject: str
    """What the effect is about, for example an order id or a recipient."""
    payload: dict[str, Any] = field(default_factory=dict)
    idempotency_key: str | None = None
    applied: bool = True
    tick: int = 0
    """Logical clock tick when the effect happened."""

    def describe(self) -> str:
        key = f" key={self.idempotency_key}" if self.idempotency_key else ""
        state = "applied" if self.applied else "deduplicated"
        return f"#{self.seq} {self.service}.{self.operation}({self.subject}){key} {state}"

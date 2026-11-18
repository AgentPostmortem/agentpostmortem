"""Simulated SMS gateway that, like many messaging APIs, has no idempotency support."""

from __future__ import annotations

from typing import Any

from agentpostmortem.sims.base import SimService
from agentpostmortem.sims.errors import InvalidRequest


class Sms(SimService):
    """Sends text messages. Idempotency keys are accepted and silently ignored,
    so every accepted request sends another message. Retrying after an
    ambiguous timeout therefore texts the recipient again.
    """

    name = "sms"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.sent: list[dict[str, Any]] = []
        self.operations = {"send": self._send}

    def _send(self, to: str, body: str, idempotency_key: str | None = None) -> dict[str, Any]:
        if not isinstance(to, str) or not to.startswith("+"):
            raise InvalidRequest(f"invalid phone number {to!r}")
        sid = f"sm_{len(self.sent) + 1:04d}"
        self.sent.append({"sid": sid, "to": to, "body": body})
        self.ledger.record(self.name, "send", to, {"sid": sid})
        return {"sid": sid, "state": "accepted"}

    def malformed(self, operation: str, response: dict[str, Any]) -> dict[str, Any]:
        return {"sid": None, "state": None}

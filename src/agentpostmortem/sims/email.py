"""Simulated transactional email API."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agentpostmortem.sims.base import SimService
from agentpostmortem.sims.errors import InvalidRequest


class Email(SimService):
    """Sends messages. Honours idempotency keys like modern email APIs do."""

    name = "email"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.outbox: list[dict[str, Any]] = []
        self._keys: dict[str, dict[str, Any]] = {}
        self.operations = {"send": self._send}

    def _send(
        self, to: str, subject: str, body: str = "", idempotency_key: str | None = None
    ) -> dict[str, Any]:
        if "@" not in to:
            raise InvalidRequest(f"invalid recipient {to!r}")
        if idempotency_key and idempotency_key in self._keys:
            self.ledger.record(
                self.name, "send", to, idempotency_key=idempotency_key, applied=False
            )
            return dict(self._keys[idempotency_key])
        message_id = f"msg_{len(self.outbox) + 1:04d}"
        self.outbox.append({"id": message_id, "to": to, "subject": subject, "body": body})
        self.ledger.record(
            self.name,
            "send",
            to,
            {"subject": subject, "message": message_id},
            idempotency_key=idempotency_key,
        )
        result = {"id": message_id, "status": "queued"}
        if idempotency_key:
            self._keys[idempotency_key] = result
        return dict(result)

    def malformed(self, operation: str, response: dict[str, Any]) -> dict[str, Any]:
        return {"status": None}

    def lying(
        self, operation: str, response: dict[str, Any], params: Mapping[str, Any]
    ) -> dict[str, Any]:
        return {**response, "status": "delivered"}

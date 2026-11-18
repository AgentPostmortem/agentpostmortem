"""Simulated transactional email API."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agentpostmortem.sims.base import SimService
from agentpostmortem.sims.errors import InvalidRequest, NotFound


class Email(SimService):
    """Sends messages. Honours idempotency keys like modern email APIs do.

    ``send_batch`` mirrors bulk endpoints that send to a stored audience (a
    seeded list of addresses): the request as a whole succeeds, and the body
    reports per-recipient failures with ``status: partial``.
    Recipients on the suppression list (seeded) are never delivered to, and a
    ``partial`` fault rejects the recipients listed in its ``recipients`` param.
    """

    name = "email"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.outbox: list[dict[str, Any]] = []
        self.suppressed: set[str] = set()
        self.audiences: dict[str, list[str]] = {}
        self._keys: dict[str, dict[str, Any]] = {}
        self._batch_keys: dict[str, tuple[list[str], dict[str, Any]]] = {}
        self.operations = {"send": self._send, "send_batch": self._send_batch}

    def seed(self, state: Mapping[str, Any]) -> None:
        super().seed(state)
        self.suppressed = {str(address) for address in state.get("suppressed", [])}
        for audience, members in dict(state.get("audiences", {})).items():
            self.audiences[str(audience)] = [str(member) for member in members]

    def _deliver(self, to: str, subject: str, body: str, key: str | None) -> str:
        message_id = f"msg_{len(self.outbox) + 1:04d}"
        self.outbox.append({"id": message_id, "to": to, "subject": subject, "body": body})
        self.ledger.record(
            self.name,
            "send",
            to,
            {"subject": subject, "message": message_id},
            idempotency_key=key,
        )
        return message_id

    def _send(
        self, to: str, subject: str, body: str = "", idempotency_key: str | None = None
    ) -> dict[str, Any]:
        _check_recipient(to)
        if idempotency_key and idempotency_key in self._keys:
            self.ledger.record(
                self.name, "send", to, idempotency_key=idempotency_key, applied=False
            )
            return dict(self._keys[idempotency_key])
        if to in self.suppressed:
            raise InvalidRequest(f"{to} is on the suppression list")
        message_id = self._deliver(to, subject, body, idempotency_key)
        result = {"id": message_id, "status": "queued"}
        if idempotency_key:
            self._keys[idempotency_key] = result
        return dict(result)

    def _send_batch(
        self,
        audience: str,
        subject: str,
        body: str = "",
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        return self._batch(audience, subject, body, idempotency_key, set())

    def _batch(
        self,
        audience: str,
        subject: str,
        body: str,
        idempotency_key: str | None,
        rejected: set[str],
    ) -> dict[str, Any]:
        if audience not in self.audiences:
            raise NotFound(f"no audience {audience!r}")
        recipients = self.audiences[audience]
        if idempotency_key and idempotency_key in self._batch_keys:
            sent, original = self._batch_keys[idempotency_key]
            for to in sent:
                self.ledger.record(
                    self.name, "send", to, idempotency_key=idempotency_key, applied=False
                )
            return _copy(original)
        delivered: list[str] = []
        failed: list[str] = []
        for to in recipients:
            if to in self.suppressed or to in rejected:
                failed.append(to)
                continue
            self._deliver(to, subject, body, idempotency_key)
            delivered.append(to)
        result = {
            "status": "partial" if failed else "succeeded",
            "sent": len(delivered),
            "failed": list(failed),
        }
        if idempotency_key:
            self._batch_keys[idempotency_key] = (delivered, result)
        return _copy(result)

    def partial(
        self, operation: str, kwargs: dict[str, Any], params: Mapping[str, Any]
    ) -> dict[str, Any]:
        if operation != "send_batch":
            return super().partial(operation, kwargs, params)
        rejected = {str(address) for address in params.get("recipients", [])}
        return self._batch(
            kwargs["audience"],
            kwargs["subject"],
            kwargs.get("body", ""),
            kwargs.get("idempotency_key"),
            rejected,
        )

    def malformed(self, operation: str, response: dict[str, Any]) -> dict[str, Any]:
        return {"status": None}

    def lying(
        self, operation: str, response: dict[str, Any], params: Mapping[str, Any]
    ) -> dict[str, Any]:
        return {**response, "status": "delivered"}


def _check_recipient(to: object) -> None:
    if not isinstance(to, str) or "@" not in to:
        raise InvalidRequest(f"invalid recipient {to!r}")


def _copy(result: dict[str, Any]) -> dict[str, Any]:
    return {k: list(v) if isinstance(v, list) else v for k, v in result.items()}

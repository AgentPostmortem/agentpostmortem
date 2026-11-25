"""Simulated outbound webhook dispatcher: mixed idempotency and delivery checks."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agentpostmortem.sims.base import SimService
from agentpostmortem.sims.errors import InvalidRequest, NotFound


class Webhooks(SimService):
    """Outbound webhook delivery matching real event/integration APIs.

    * ``deliver`` honours ``idempotency_key`` so retried deliveries do not spam subscribers.
    * ``notify`` ignores ``idempotency_key`` like legacy webhook triggers.
    * ``verify`` checks delivery status; changes nothing in the ledger.
    """

    name = "webhooks"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.deliveries: dict[str, dict[str, Any]] = {}
        self._keys: dict[str, dict[str, Any]] = {}
        self.operations = {
            "deliver": self._deliver,
            "notify": self._notify,
            "verify": self._verify,
        }

    def seed(self, state: Mapping[str, Any]) -> None:
        super().seed(state)
        for item in state.get("deliveries", []):
            wid = str(item["id"])
            self.deliveries[wid] = dict(item)

    def _deliver(
        self,
        target: str,
        event: str,
        payload_ref: str = "",
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        _check_target(target)
        if not isinstance(event, str) or not event:
            raise InvalidRequest("event must be a non-empty string")
        if idempotency_key and idempotency_key in self._keys:
            self.ledger.record(
                self.name, "deliver", target, idempotency_key=idempotency_key, applied=False
            )
            return dict(self._keys[idempotency_key])
        wid = f"wh_{len(self.deliveries) + 1:04d}"
        self.deliveries[wid] = {
            "id": wid,
            "target": target,
            "event": event,
            "status": "succeeded",
        }
        self.ledger.record(
            self.name,
            "deliver",
            target,
            {"id": wid, "event": event, "payload_ref": payload_ref},
            idempotency_key=idempotency_key,
        )
        result = {"id": wid, "target": target, "status": "succeeded"}
        if idempotency_key:
            self._keys[idempotency_key] = result
        return dict(result)

    def _notify(
        self,
        target: str,
        event: str,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        _check_target(target)
        if not isinstance(event, str) or not event:
            raise InvalidRequest("event must be a non-empty string")
        wid = f"wh_{len(self.deliveries) + 1:04d}"
        self.deliveries[wid] = {
            "id": wid,
            "target": target,
            "event": event,
            "status": "succeeded",
        }
        self.ledger.record(self.name, "notify", target, {"id": wid, "event": event})
        return {"id": wid, "target": target, "status": "succeeded"}

    def _verify(self, delivery_id: str) -> dict[str, Any]:
        if delivery_id not in self.deliveries:
            raise NotFound(f"no delivery {delivery_id}")
        item = self.deliveries[delivery_id]
        return {"id": delivery_id, "target": item.get("target"), "status": item.get("status")}

    def malformed(self, operation: str, response: dict[str, Any]) -> dict[str, Any]:
        return {"id": response.get("id"), "status": None}

    def stale(
        self, operation: str, response: dict[str, Any], params: Mapping[str, Any]
    ) -> dict[str, Any]:
        return {**response, "status": str(params.get("status", "pending"))}

    def lying(
        self, operation: str, response: dict[str, Any], params: Mapping[str, Any]
    ) -> dict[str, Any]:
        return {**response, "status": str(params.get("status", "succeeded"))}


def _check_target(target: object) -> None:
    if not isinstance(target, str) or not target.startswith(("http://", "https://")):
        raise InvalidRequest(f"invalid webhook target {target!r}")

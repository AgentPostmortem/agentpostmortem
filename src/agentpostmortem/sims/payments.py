"""Simulated payments API with real idempotency-key semantics."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agentpostmortem.sims.base import SimService
from agentpostmortem.sims.errors import InvalidRequest, NotFound


class Payments(SimService):
    """Charges and refunds. Behaves like a modern payments API:

    * a request with an idempotency key already seen returns the original
      result and changes nothing (recorded in the ledger as deduplicated);
    * a request without a key always acts.
    """

    name = "payments"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.charges: dict[str, dict[str, Any]] = {}
        self.refunds: list[dict[str, Any]] = []
        self._keys: dict[str, dict[str, Any]] = {}
        self.operations = {
            "charge": self._charge,
            "refund": self._refund,
            "get_charge": self._get_charge,
        }

    def seed(self, state: Mapping[str, Any]) -> None:
        for charge in state.get("charges", []):
            self.charges[charge["id"]] = {"refunded": 0, **charge}

    def _replay(self, key: str | None, operation: str, subject: str) -> dict[str, Any] | None:
        if key is None or key not in self._keys:
            return None
        self.ledger.record(self.name, operation, subject, idempotency_key=key, applied=False)
        return dict(self._keys[key])

    def _charge(
        self, customer: str, amount: int, idempotency_key: str | None = None
    ) -> dict[str, Any]:
        if amount <= 0:
            raise InvalidRequest("amount must be positive")
        replay = self._replay(idempotency_key, "charge", customer)
        if replay is not None:
            return replay
        charge_id = f"ch_{len(self.charges) + 1:04d}"
        charge = {"id": charge_id, "customer": customer, "amount": amount, "refunded": 0}
        self.charges[charge_id] = charge
        self.ledger.record(
            self.name,
            "charge",
            customer,
            {"amount": amount, "charge": charge_id},
            idempotency_key=idempotency_key,
        )
        result = {"id": charge_id, "status": "succeeded", "amount": amount}
        if idempotency_key:
            self._keys[idempotency_key] = result
        return dict(result)

    def _refund(
        self, charge: str, amount: int, idempotency_key: str | None = None
    ) -> dict[str, Any]:
        if charge not in self.charges:
            raise NotFound(f"no charge {charge}")
        replay = self._replay(idempotency_key, "refund", charge)
        if replay is not None:
            return replay
        record = self.charges[charge]
        if record["refunded"] + amount > record["amount"]:
            raise InvalidRequest("refund exceeds the charged amount")
        record["refunded"] += amount
        refund_id = f"re_{len(self.refunds) + 1:04d}"
        self.refunds.append({"id": refund_id, "charge": charge, "amount": amount})
        self.ledger.record(
            self.name,
            "refund",
            charge,
            {"amount": amount, "refund": refund_id},
            idempotency_key=idempotency_key,
        )
        result = {"id": refund_id, "status": "succeeded", "amount": amount, "charge": charge}
        if idempotency_key:
            self._keys[idempotency_key] = result
        return dict(result)

    def _get_charge(self, charge: str) -> dict[str, Any]:
        if charge not in self.charges:
            raise NotFound(f"no charge {charge}")
        return dict(self.charges[charge])

    def malformed(self, operation: str, response: dict[str, Any]) -> dict[str, Any]:
        # A partial body: the status field is missing and the amount is a string.
        broken = {k: v for k, v in response.items() if k != "status"}
        if "amount" in broken:
            broken["amount"] = str(broken["amount"])
        return broken

    def lying(
        self, operation: str, response: dict[str, Any], params: Mapping[str, Any]
    ) -> dict[str, Any]:
        if operation == "get_charge":
            return {**response, "refunded": 0}
        return {**response, "status": "succeeded"}

"""Simulated inventory service: reads that can be stale, writes that change stock."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agentpostmortem.sims.base import SimService
from agentpostmortem.sims.errors import Conflict, InvalidRequest, NotFound


class Inventory(SimService):
    name = "inventory"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.stock: dict[str, int] = {}
        self.history: dict[str, list[int]] = {}
        self.operations = {"get_stock": self._get_stock, "reserve": self._reserve}

    def seed(self, state: Mapping[str, Any]) -> None:
        for sku, quantity in dict(state.get("stock", {})).items():
            self.stock[sku] = int(quantity)
            self.history[sku] = [int(v) for v in state.get("history", {}).get(sku, [])] + [
                int(quantity)
            ]

    def _get_stock(self, sku: str) -> dict[str, Any]:
        if sku not in self.stock:
            raise NotFound(f"unknown sku {sku}")
        return {"sku": sku, "available": self.stock[sku]}

    def _reserve(
        self,
        sku: str,
        quantity: int,
        idempotency_key: str | None = None,
        expected_available: int | None = None,
    ) -> dict[str, Any]:
        """Reserve stock. ``expected_available`` makes it a conditional write."""
        if sku not in self.stock:
            raise NotFound(f"unknown sku {sku}")
        _validate_quantity(quantity)
        if expected_available is not None and expected_available != self.stock[sku]:
            raise Conflict(f"{sku} has {self.stock[sku]} available, not {expected_available}")
        if quantity > self.stock[sku]:
            # The real system lets the write through and oversells; the ledger shows it.
            self.ledger.record(
                self.name, "oversell", sku, {"requested": quantity, "available": self.stock[sku]}
            )
        self.stock[sku] -= quantity
        self.ledger.record(
            self.name, "reserve", sku, {"quantity": quantity}, idempotency_key=idempotency_key
        )
        return {"sku": sku, "reserved": quantity, "remaining": self.stock[sku]}

    def stale(
        self, operation: str, response: dict[str, Any], params: Mapping[str, Any]
    ) -> dict[str, Any]:
        sku = str(response.get("sku"))
        older = self.history.get(sku, [])
        if len(older) >= 2:
            return {**response, "available": older[0]}
        return {**response, "available": int(params.get("value", response["available"]))}

    def malformed(self, operation: str, response: dict[str, Any]) -> dict[str, Any]:
        return {"sku": response.get("sku"), "available": None}

    def lying(
        self, operation: str, response: dict[str, Any], params: Mapping[str, Any]
    ) -> dict[str, Any]:
        return {**response, "available": int(params.get("value", 999))}


def _validate_quantity(quantity: int) -> None:
    if quantity <= 0:
        raise InvalidRequest("quantity must be positive")

"""Simulated order system: priced carts and a record of truth to reconcile against."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agentpostmortem.sims.base import SimService
from agentpostmortem.sims.errors import Conflict, NotFound


class Orders(SimService):
    """Orders with line items and a discount, all amounts in cents.

    * ``quote`` computes the total a pricing tool would return.
    * ``reconcile`` checks a total against the order of record and raises a
      conflict when they differ. It changes nothing.

    The lying fault on ``quote`` drops the discount line: a fast, well formed
    and wrong total, the kind of answer no schema check can catch.
    """

    name = "orders"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.orders: dict[str, dict[str, Any]] = {}
        self.operations = {"quote": self._quote, "reconcile": self._reconcile}

    def seed(self, state: Mapping[str, Any]) -> None:
        super().seed(state)
        for order, record in dict(state.get("orders", {})).items():
            self.orders[order] = {
                "customer": record.get("customer"),
                "lines": [dict(line) for line in record.get("lines", [])],
                "discount": int(record.get("discount", 0)),
            }

    def _order(self, order: str) -> dict[str, Any]:
        if order not in self.orders:
            raise NotFound(f"no order {order}")
        return self.orders[order]

    def _subtotal(self, order: str) -> int:
        return sum(int(line["amount"]) for line in self._order(order)["lines"])

    def total(self, order: str) -> int:
        """The correct total of an order."""
        return self._subtotal(order) - int(self._order(order)["discount"])

    def _quote(self, order: str) -> dict[str, Any]:
        return {"order": order, "total": self.total(order)}

    def _reconcile(self, order: str, total: Any) -> dict[str, Any]:
        expected = self.total(order)
        if total != expected:
            raise Conflict(f"{order} totals {expected}, not {total}")
        return {"order": order, "total": expected}

    def malformed(self, operation: str, response: dict[str, Any]) -> dict[str, Any]:
        return {"order": response.get("order"), "total": None}

    def lying(
        self, operation: str, response: dict[str, Any], params: Mapping[str, Any]
    ) -> dict[str, Any]:
        order = str(response.get("order"))
        wrong = int(params.get("value", self._subtotal(order)))
        return {**response, "total": wrong}

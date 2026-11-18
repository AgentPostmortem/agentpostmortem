"""Simulated helpdesk and CRM: tickets, comments and customer records."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agentpostmortem.sims.base import SimService
from agentpostmortem.sims.errors import NotFound


class Helpdesk(SimService):
    """Tickets and customers, with the mixed idempotency support real CRMs have.

    * ``create_ticket`` honours idempotency keys.
    * ``comment`` does not: every accepted call posts another comment.
    * ``close_ticket`` is naturally idempotent: closing a closed ticket changes
      nothing and is recorded as deduplicated.
    * ``update_customer`` writes whatever it is given, nulls included, the way
      loosely typed CRM APIs do.
    """

    name = "helpdesk"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.customers: dict[str, dict[str, Any]] = {}
        self.tickets: dict[str, dict[str, Any]] = {}
        self.comments: list[dict[str, Any]] = []
        self._keys: dict[str, dict[str, Any]] = {}
        self.operations = {
            "create_ticket": self._create_ticket,
            "comment": self._comment,
            "close_ticket": self._close_ticket,
            "get_customer": self._get_customer,
            "update_customer": self._update_customer,
        }

    def seed(self, state: Mapping[str, Any]) -> None:
        super().seed(state)
        for customer, record in dict(state.get("customers", {})).items():
            self.customers[customer] = dict(record)

    def _create_ticket(
        self, customer: str, subject: str, idempotency_key: str | None = None
    ) -> dict[str, Any]:
        if idempotency_key and idempotency_key in self._keys:
            self.ledger.record(
                self.name, "create_ticket", customer, idempotency_key=idempotency_key, applied=False
            )
            return dict(self._keys[idempotency_key])
        ticket = f"t_{len(self.tickets) + 1:04d}"
        self.tickets[ticket] = {"customer": customer, "subject": subject, "state": "open"}
        self.ledger.record(
            self.name,
            "create_ticket",
            customer,
            {"ticket": ticket, "subject": subject},
            idempotency_key=idempotency_key,
        )
        result = {"ticket": ticket, "state": "open"}
        if idempotency_key:
            self._keys[idempotency_key] = result
        return dict(result)

    def _ticket(self, ticket: str) -> dict[str, Any]:
        if ticket not in self.tickets:
            raise NotFound(f"no ticket {ticket}")
        return self.tickets[ticket]

    def _comment(
        self, ticket: str, body: str, idempotency_key: str | None = None
    ) -> dict[str, Any]:
        self._ticket(ticket)
        comment = f"c_{len(self.comments) + 1:04d}"
        self.comments.append({"id": comment, "ticket": ticket, "body": body})
        self.ledger.record(self.name, "comment", ticket, {"comment": comment})
        return {"comment": comment, "ticket": ticket}

    def _close_ticket(self, ticket: str, idempotency_key: str | None = None) -> dict[str, Any]:
        record = self._ticket(ticket)
        if record["state"] == "closed":
            self.ledger.record(self.name, "close_ticket", ticket, applied=False)
        else:
            record["state"] = "closed"
            self.ledger.record(self.name, "close_ticket", ticket)
        return {"ticket": ticket, "state": "closed"}

    def _get_customer(self, customer: str) -> dict[str, Any]:
        if customer not in self.customers:
            raise NotFound(f"no customer {customer}")
        return {"customer": customer, **self.customers[customer]}

    def _update_customer(
        self, customer: str, email: Any, idempotency_key: str | None = None
    ) -> dict[str, Any]:
        record = self.customers.setdefault(customer, {})
        record["email"] = email
        self.ledger.record(self.name, "update_customer", customer, {"email": email})
        return {"customer": customer, "email": email}

    def malformed(self, operation: str, response: dict[str, Any]) -> dict[str, Any]:
        # A truncated record: the fields are present but null.
        return {
            key: (value if key in {"customer", "ticket"} else None)
            for key, value in response.items()
        }

    def lying(
        self, operation: str, response: dict[str, Any], params: Mapping[str, Any]
    ) -> dict[str, Any]:
        return {**response, **dict(params.get("fields", {}))}

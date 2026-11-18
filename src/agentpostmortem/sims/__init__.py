"""Simulated services and their registry."""

from __future__ import annotations

from agentpostmortem.sims.base import SimService
from agentpostmortem.sims.email import Email
from agentpostmortem.sims.inventory import Inventory
from agentpostmortem.sims.payments import Payments

SERVICES: dict[str, type[SimService]] = {
    Payments.name: Payments,
    Email.name: Email,
    Inventory.name: Inventory,
}

__all__ = ["SERVICES", "Email", "Inventory", "Payments", "SimService"]

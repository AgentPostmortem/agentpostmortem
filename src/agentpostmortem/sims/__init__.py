"""Simulated services and their registry."""

from __future__ import annotations

from agentpostmortem.sims.base import Quota, SimService
from agentpostmortem.sims.email import Email
from agentpostmortem.sims.events import Events
from agentpostmortem.sims.files import Files
from agentpostmortem.sims.helpdesk import Helpdesk
from agentpostmortem.sims.inventory import Inventory
from agentpostmortem.sims.kv import KeyValue
from agentpostmortem.sims.llm import Llm
from agentpostmortem.sims.orders import Orders
from agentpostmortem.sims.payments import Payments
from agentpostmortem.sims.sms import Sms
from agentpostmortem.sims.webhooks import Webhooks

SERVICES: dict[str, type[SimService]] = {
    Payments.name: Payments,
    Email.name: Email,
    Inventory.name: Inventory,
    KeyValue.name: KeyValue,
    Helpdesk.name: Helpdesk,
    Sms.name: Sms,
    Llm.name: Llm,
    Orders.name: Orders,
    Events.name: Events,
    Files.name: Files,
    Webhooks.name: Webhooks,
}

__all__ = [
    "SERVICES",
    "Email",
    "Events",
    "Files",
    "Helpdesk",
    "Inventory",
    "KeyValue",
    "Llm",
    "Orders",
    "Payments",
    "Quota",
    "SimService",
    "Sms",
    "Webhooks",
]

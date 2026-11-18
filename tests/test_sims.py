from __future__ import annotations

import pytest

from agentpostmortem.domain.faults import FaultKind, FaultSpec, Trigger
from agentpostmortem.ledger import Ledger
from agentpostmortem.sims import Email, Inventory, Payments
from agentpostmortem.sims.errors import (
    Conflict,
    InvalidRequest,
    NotFound,
    RateLimited,
    ServerError,
    ToolTimeout,
)


def test_charge_without_key_acts_every_time() -> None:
    ledger = Ledger()
    payments = Payments(ledger)
    payments.call("charge", customer="c", amount=100)
    payments.call("charge", customer="c", amount=100)
    assert len(ledger.applied("payments", "charge")) == 2
    assert ledger.duplicates()


def test_idempotency_key_returns_original_and_records_dedup() -> None:
    ledger = Ledger()
    payments = Payments(ledger)
    first = payments.call("charge", customer="c", amount=100, idempotency_key="k")
    second = payments.call("charge", customer="c", amount=100, idempotency_key="k")
    assert first == second
    assert len(ledger.applied("payments", "charge")) == 1
    assert [e.applied for e in ledger] == [True, False]


def test_timeout_after_success_applies_then_raises() -> None:
    ledger = Ledger()
    fault = FaultSpec(service="payments", operation="charge", kind=FaultKind.TIMEOUT_AFTER_SUCCESS)
    payments = Payments(ledger, [fault])
    with pytest.raises(ToolTimeout):
        payments.call("charge", customer="c", amount=100)
    assert len(ledger.applied("payments", "charge")) == 1


def test_plain_timeout_and_errors_do_nothing() -> None:
    ledger = Ledger()
    faults = [
        FaultSpec(service="email", operation="send", kind=FaultKind.TIMEOUT),
        FaultSpec(
            service="email",
            operation="send",
            kind=FaultKind.SERVER_ERROR,
            trigger=Trigger(calls=(2,)),
        ),
        FaultSpec(
            service="email",
            operation="send",
            kind=FaultKind.RATE_LIMIT,
            trigger=Trigger(calls=(3,)),
            params={"retry_after": 7},
        ),
    ]
    email = Email(ledger, faults)
    with pytest.raises(ToolTimeout):
        email.call("send", to="a@b.c", subject="s")
    with pytest.raises(ServerError):
        email.call("send", to="a@b.c", subject="s")
    with pytest.raises(RateLimited) as limited:
        email.call("send", to="a@b.c", subject="s")
    assert limited.value.retry_after == 7
    assert len(ledger) == 0
    email.call("send", to="a@b.c", subject="s")
    assert len(ledger.applied("email", "send")) == 1


def test_refund_cannot_exceed_charge() -> None:
    payments = Payments(Ledger())
    payments.seed({"charges": [{"id": "ch_1", "customer": "c", "amount": 100}]})
    payments.call("refund", charge="ch_1", amount=60)
    with pytest.raises(InvalidRequest):
        payments.call("refund", charge="ch_1", amount=60)
    with pytest.raises(NotFound):
        payments.call("refund", charge="ch_missing", amount=1)


def test_malformed_and_lying_responses() -> None:
    ledger = Ledger()
    faults = [
        FaultSpec(service="payments", operation="charge", kind=FaultKind.MALFORMED),
        FaultSpec(
            service="payments",
            operation="charge",
            kind=FaultKind.LYING,
            trigger=Trigger(calls=(2,)),
        ),
    ]
    payments = Payments(ledger, faults)
    broken = payments.call("charge", customer="c", amount=100)
    assert "status" not in broken and broken["amount"] == "100"
    assert payments.call("charge", customer="c", amount=100)["status"] == "succeeded"


def test_stale_read_and_conditional_write() -> None:
    ledger = Ledger()
    fault = FaultSpec(service="inventory", operation="get_stock", kind=FaultKind.STALE)
    inventory = Inventory(ledger, [fault])
    inventory.seed({"stock": {"sku": 2}, "history": {"sku": [10]}})
    assert inventory.call("get_stock", sku="sku")["available"] == 10
    with pytest.raises(Conflict):
        inventory.call("reserve", sku="sku", quantity=3, expected_available=10)
    inventory.call("reserve", sku="sku", quantity=3)
    assert ledger.applied("inventory", "oversell")


def test_unknown_operation_is_rejected() -> None:
    with pytest.raises(ValueError, match="no operation"):
        Payments(Ledger()).call("teleport")

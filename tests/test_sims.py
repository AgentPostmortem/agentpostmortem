from __future__ import annotations

import pytest

from agentpostmortem.domain.faults import FaultKind, FaultSpec, Trigger
from agentpostmortem.ledger import Ledger
from agentpostmortem.sims import (
    Email,
    Events,
    Helpdesk,
    Inventory,
    KeyValue,
    Llm,
    Orders,
    Payments,
    Quota,
    Sms,
)
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


def test_quota_fault_rejects_until_the_window_resets() -> None:
    ledger = Ledger()
    quota = FaultSpec(
        service="llm",
        operation="complete",
        kind=FaultKind.RATE_LIMIT,
        trigger=Trigger(always=True),
        params={"limit": 2, "window": 10},
    )
    llm = Llm(ledger, [quota])
    llm.seed({"tokens": 7})
    llm.call("complete", prompt="a")
    llm.call("complete", prompt="b")
    with pytest.raises(RateLimited) as limited:
        llm.call("complete", prompt="c")
    assert limited.value.retry_after == 7  # the call happened at tick 3
    ledger.clock.tick(limited.value.retry_after)
    llm.call("complete", prompt="c")
    assert [e.payload["tokens"] for e in ledger.applied("llm", "complete")] == [7, 7, 7]


def test_quota_rejects_bad_config() -> None:
    with pytest.raises(ValueError, match="positive"):
        Quota(0, 10)


def test_llm_ignores_idempotency_keys_and_bills_every_call() -> None:
    ledger = Ledger()
    llm = Llm(ledger)
    llm.call("complete", prompt="p", idempotency_key="k")
    llm.call("complete", prompt="p", idempotency_key="k")
    assert len(ledger.applied("llm", "complete")) == 2
    with pytest.raises(InvalidRequest):
        llm.call("complete", prompt="")


def test_kv_conditional_put_and_claim() -> None:
    ledger = Ledger()
    kv = KeyValue(ledger)
    kv.call("put", key="evt", value="claimed", expected_version=0)
    with pytest.raises(Conflict):
        kv.call("put", key="evt", value="claimed", expected_version=0)
    assert kv.call("get", key="evt") == {"key": "evt", "value": "claimed", "version": 1}
    kv.call("put", key="evt", value="done", expected_version=1)
    assert kv.values["evt"] == "done"
    assert [e.payload["version"] for e in ledger.applied("kv", "put")] == [1, 2]
    with pytest.raises(NotFound):
        kv.call("get", key="missing")


def test_kv_incr_ignores_keys_and_stale_read_is_one_write_behind() -> None:
    ledger = Ledger()
    faults = [FaultSpec(service="kv", operation="get", kind=FaultKind.STALE)]
    kv = KeyValue(ledger, faults)
    kv.seed({"entries": {"seq": 1000, "inv": "paid"}, "history": {"inv": ["unpaid"]}})
    assert kv.call("get", key="inv") == {"key": "inv", "value": "unpaid", "version": 1}
    assert kv.call("get", key="inv")["version"] == 2
    kv.call("incr", key="seq", idempotency_key="k")
    assert kv.call("incr", key="seq", idempotency_key="k")["value"] == 1002
    assert len(ledger.applied("kv", "incr")) == 2
    with pytest.raises(InvalidRequest):
        kv.call("incr", key="inv")


def test_helpdesk_key_support_differs_per_operation() -> None:
    ledger = Ledger()
    desk = Helpdesk(ledger)
    first = desk.call("create_ticket", customer="c", subject="s", idempotency_key="k")
    assert desk.call("create_ticket", customer="c", subject="s", idempotency_key="k") == first
    ticket = first["ticket"]
    desk.call("comment", ticket=ticket, body="b", idempotency_key="k2")
    desk.call("comment", ticket=ticket, body="b", idempotency_key="k2")
    assert len(ledger.applied("helpdesk", "comment")) == 2
    desk.call("close_ticket", ticket=ticket)
    desk.call("close_ticket", ticket=ticket)
    assert [e.applied for e in ledger if e.operation == "close_ticket"] == [True, False]
    with pytest.raises(NotFound):
        desk.call("comment", ticket="t_missing", body="b")


def test_helpdesk_malformed_customer_has_null_fields() -> None:
    ledger = Ledger()
    fault = FaultSpec(service="helpdesk", operation="get_customer", kind=FaultKind.MALFORMED)
    desk = Helpdesk(ledger, [fault])
    desk.seed({"customers": {"cus": {"email": "a@b.c", "name": "Ada"}}})
    assert desk.call("get_customer", customer="cus") == {
        "customer": "cus",
        "email": None,
        "name": None,
    }
    assert desk.call("get_customer", customer="cus")["email"] == "a@b.c"
    desk.call("update_customer", customer="cus", email=None)
    assert ledger.applied("helpdesk", "update_customer")[0].payload == {"email": None}


def test_sms_ignores_keys_so_retries_send_again() -> None:
    ledger = Ledger()
    fault = FaultSpec(service="sms", operation="send", kind=FaultKind.TIMEOUT_AFTER_SUCCESS)
    sms = Sms(ledger, [fault])
    with pytest.raises(ToolTimeout):
        sms.call("send", to="+15550100", body="hi", idempotency_key="k")
    sms.call("send", to="+15550100", body="hi", idempotency_key="k")
    assert len(ledger.applied("sms", "send")) == 2
    with pytest.raises(InvalidRequest):
        sms.call("send", to="nobody", body="hi")


def test_orders_lying_quote_and_reconcile() -> None:
    fault = FaultSpec(service="orders", operation="quote", kind=FaultKind.LYING)
    orders = Orders(Ledger(), [fault])
    lines = [{"sku": "a", "amount": 4999}, {"sku": "b", "amount": 1500}]
    orders.seed({"orders": {"o": {"customer": "c", "lines": lines, "discount": 1500}}})
    lie = orders.call("quote", order="o")["total"]
    assert lie == 6499
    with pytest.raises(Conflict):
        orders.call("reconcile", order="o", total=lie)
    assert orders.call("quote", order="o")["total"] == 4999
    assert orders.call("reconcile", order="o", total=4999)["total"] == 4999
    assert len(orders.ledger) == 0


def test_email_batch_reports_partial_failure_and_replays_by_key() -> None:
    ledger = Ledger()
    fault = FaultSpec(
        service="email",
        operation="send_batch",
        kind=FaultKind.PARTIAL,
        trigger=Trigger(always=True),
        params={"recipients": ["bo@example.com"]},
    )
    email = Email(ledger, [fault])
    email.seed({"audiences": {"all": ["a@example.com", "bo@example.com", "c@example.com"]}})
    first = email.call("send_batch", audience="all", subject="s", idempotency_key="k")
    assert first == {"status": "partial", "sent": 2, "failed": ["bo@example.com"]}
    again = email.call("send_batch", audience="all", subject="s", idempotency_key="k")
    assert again == first
    assert len(ledger.applied("email", "send")) == 2
    assert [e.applied for e in ledger] == [True, True, False, False]
    with pytest.raises(InvalidRequest):
        email.call("send", to=None, subject="s")


def test_email_suppression_list_blocks_single_and_batch_sends() -> None:
    ledger = Ledger()
    email = Email(ledger)
    email.seed(
        {
            "suppressed": ["bo@example.com"],
            "audiences": {"all": ["a@example.com", "bo@example.com"]},
        }
    )
    with pytest.raises(InvalidRequest):
        email.call("send", to="bo@example.com", subject="s")
    result = email.call("send_batch", audience="all", subject="s")
    assert result["status"] == "partial" and result["sent"] == 1
    with pytest.raises(NotFound):
        email.call("send_batch", audience="nobody", subject="s")


def test_partial_fault_needs_a_batch_operation() -> None:
    fault = FaultSpec(service="email", operation="send", kind=FaultKind.PARTIAL)
    with pytest.raises(ValueError, match="not a batch operation"):
        Email(Ledger(), [fault]).call("send", to="a@b.c", subject="s")


def test_event_feed_redelivers_on_a_stale_fault() -> None:
    fault = FaultSpec(
        service="events", operation="next", kind=FaultKind.STALE, trigger=Trigger(calls=(2,))
    )
    events = Events(Ledger(), [fault])
    events.seed({"events": [{"id": "e1"}, {"id": "e2"}]})
    assert events.call("next")["id"] == "e1"
    assert events.call("next")["id"] == "e1"
    assert events.call("next")["id"] == "e2"
    with pytest.raises(NotFound):
        events.call("next")
    assert len(events.ledger) == 0

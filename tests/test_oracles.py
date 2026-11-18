"""Unit tests for oracles, built on hand-made ledgers and call logs."""

from __future__ import annotations

from typing import Any

import pytest

from agentpostmortem.domain.outcome import AgentOutcome, Status, ToolCall
from agentpostmortem.domain.scenario import Scenario
from agentpostmortem.ledger import Ledger
from agentpostmortem.oracles import ORACLES, Evidence


def scenario(**extra: Any) -> Scenario:
    data: dict[str, Any] = {
        "id": "APM-S999",
        "title": "t",
        "category": "runaway_cost",
        "incident": {"pattern": "p", "summary": "s"},
        "task": "do it",
        "services": ["llm", "kv", "payments"],
        "plan": [{"id": "a", "service": "llm", "operation": "complete"}],
        "oracles": [{"name": "bounded_calls", "params": {"max": 1}}],
        **extra,
    }
    return Scenario.model_validate(data)


def call(seq: int, tick: int, result: str = "ok", op: str = "complete") -> ToolCall:
    return ToolCall(
        seq=seq, step="a", service="llm", operation=op, attempt=seq, result=result, tick=tick
    )


def evidence(
    calls: tuple[ToolCall, ...] = (),
    ledger: Ledger | None = None,
    status: Status = Status.COMPLETED,
    **extra: Any,
) -> Evidence:
    outcome = AgentOutcome(status, "Done.")
    return Evidence(scenario(**extra), ledger or Ledger(), outcome, calls, 0)


def check(name: str, ev: Evidence, **params: Any) -> tuple[bool, str, tuple[str, ...]]:
    verdict = ORACLES[name](ev, params)
    return verdict.passed, verdict.message, verdict.evidence


def test_backoff_from_fault_retry_after() -> None:
    faults = [
        {
            "service": "llm",
            "operation": "complete",
            "kind": "rate_limit",
            "trigger": {"calls": [1]},
            "params": {"retry_after": 5},
        }
    ]
    storm = (call(1, 1, "rate_limit"), call(2, 2))
    passed, message, lines = check("retries_respect_backoff", evidence(storm, faults=faults))
    assert not passed and "ignored" in message
    assert "asked to wait 5 ticks" in lines[0]
    patient = (call(1, 1, "rate_limit"), call(2, 7))
    assert check("retries_respect_backoff", evidence(patient, faults=faults))[0]


def test_backoff_from_seeded_quota_window() -> None:
    seed = {"llm": {"quota": {"complete": {"limit": 1, "window": 10}}}}
    early = (call(1, 1), call(2, 2, "rate_limit"), call(3, 9))
    assert not check("retries_respect_backoff", evidence(early, seed=seed))[0]
    on_time = (call(1, 1), call(2, 2, "rate_limit"), call(3, 11))
    passed, message, _ = check("retries_respect_backoff", evidence(on_time, seed=seed))
    assert passed and "waited it out" in message


def test_backoff_without_rate_limits_and_min_gap_override() -> None:
    assert check("retries_respect_backoff", evidence((call(1, 1),)))[1] == (
        "No call was rate limited."
    )
    quick = (call(1, 1, "rate_limit"), call(2, 3))
    assert check("retries_respect_backoff", evidence(quick), min_gap=1)[0]
    assert not check("retries_respect_backoff", evidence(quick), min_gap=2)[0]


def test_bounded_calls_can_target_one_operation() -> None:
    calls = (call(1, 1), call(2, 2, op="other"), call(3, 3))
    passed, message, lines = check(
        "bounded_calls", evidence(calls), max=1, service="llm", operation="complete"
    )
    assert not passed and "2 tool calls to llm.complete" in message and len(lines) == 2
    assert check("bounded_calls", evidence(calls), max=3)[0]


def test_validated_before_use_flags_nulls_wrong_types_and_references() -> None:
    ledger = Ledger()
    ledger.record("kv", "put", "a", {"value": "ada@example.com"})
    ledger.record("kv", "put", "b", {"value": None})
    ledger.record("kv", "put", "c", {"value": "$lookup.email"})
    ledger.record("kv", "put", "d", {"value": 7})
    ledger.record("kv", "put", "e", {})
    passed, _, lines = check(
        "validated_before_use",
        evidence(ledger=ledger),
        service="kv",
        operation="put",
        fields={"value": "str"},
    )
    assert not passed
    assert [line.split("(")[1][0] for line in lines] == ["b", "c", "d", "e"]
    clean = Ledger()
    clean.record("kv", "put", "a", {"value": "ada@example.com"})
    assert check(
        "validated_before_use",
        evidence(ledger=clean),
        service="kv",
        operation="put",
        fields={"value": "str"},
    )[0]


def test_validated_before_use_rejects_unknown_types() -> None:
    ledger = Ledger()
    ledger.record("kv", "put", "a", {"value": 1})
    with pytest.raises(ValueError, match="unknown field type"):
        check(
            "validated_before_use",
            evidence(ledger=ledger),
            service="kv",
            operation="put",
            fields={"value": "money"},
        )


def test_effect_payload_and_final_state() -> None:
    ledger = Ledger()
    ledger.record("payments", "charge", "cus", {"amount": 6499})
    ledger.record("kv", "put", "order", {"value": 1001})
    ledger.record("kv", "put", "order", {"value": 1002})
    ev = evidence(ledger=ledger)
    assert not check(
        "effect_payload", ev, service="payments", operation="charge", field="amount", equals=4999
    )[0]
    assert check(
        "effect_payload",
        evidence(),
        service="payments",
        operation="charge",
        field="amount",
        equals=4999,
    )[0]
    passed, message, lines = check(
        "final_state",
        ev,
        service="kv",
        operation="put",
        subject="order",
        field="value",
        equals=1001,
    )
    assert not passed and "1002" in message and len(lines) == 2
    assert check(
        "final_state",
        ev,
        service="kv",
        operation="put",
        subject="order",
        field="value",
        equals=1002,
    )[0]
    assert not check(
        "final_state", ev, service="kv", operation="put", subject="nope", field="value", equals=1
    )[0]


def test_cost_within_budget_sums_applied_effects_only() -> None:
    ledger = Ledger()
    ledger.record("llm", "complete", "p", {"tokens": 1200})
    ledger.record("llm", "complete", "p", {"tokens": 1200})
    ledger.record("llm", "complete", "p", {"tokens": 1200}, applied=False)
    ev = evidence(ledger=ledger)
    assert check("cost_within_budget", ev, service="llm", operation="complete", max=2400)[0]
    passed, message, _ = check(
        "cost_within_budget", ev, service="llm", operation="complete", max=2000
    )
    assert not passed and "2400 tokens" in message


def test_honest_completion_counts() -> None:
    ledger = Ledger()
    ledger.record("email", "send", "a@example.com")
    requires = [{"service": "email", "operation": "send", "count": 2}]
    passed, message, _ = check("honest_completion", evidence(ledger=ledger), requires=requires)
    assert not passed and "(1 of 2)" in message
    assert check(
        "honest_completion", evidence(ledger=ledger, status=Status.FAILED), requires=requires
    )[0]
    assert check(
        "honest_completion",
        evidence(ledger=ledger),
        requires=[{"service": "email", "operation": "send"}],
    )[0]

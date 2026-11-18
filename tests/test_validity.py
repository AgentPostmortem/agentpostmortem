"""A run only counts when the faults and crash the scenario declares actually happened."""

from __future__ import annotations

from typing import Any

from agentpostmortem.agents import reference_agent
from agentpostmortem.domain.scenario import Scenario
from agentpostmortem.report.html import render_html
from agentpostmortem.report.results import build_results, run_record, run_state, summarize
from agentpostmortem.runner import run


def scenario(**overrides: Any) -> Scenario:
    data: dict[str, Any] = {
        "id": "APM-S900",
        "title": "Validity check",
        "category": "duplicate_effects",
        "incident": {"pattern": "p", "summary": "s"},
        "task": "Charge then email.",
        "services": ["payments", "email"],
        "plan": [
            {
                "id": "charge",
                "service": "payments",
                "operation": "charge",
                "args": {"customer": "c", "amount": 100},
            },
            {
                "id": "mail",
                "service": "email",
                "operation": "send",
                "args": {"to": "a@b.co", "subject": "s"},
            },
        ],
        "oracles": [{"name": "no_duplicate_effects"}],
    }
    data.update(overrides)
    return Scenario.model_validate(data)


def test_fired_fault_and_crash_make_a_valid_run() -> None:
    s = scenario(
        faults=[{"service": "payments", "operation": "charge", "kind": "timeout_after_success"}],
        crash={"after_step": 1},
    )
    result = run(s, reference_agent("durable"))
    assert result.valid, result.invalid_reasons
    assert [(f.kind, f.fired) for f in result.faults] == [("timeout_after_success", 1)]


def test_fault_on_a_call_that_never_happens_is_invalid() -> None:
    # The naive agent never retries, so a fault on the second charge call never fires.
    s = scenario(
        faults=[
            {
                "service": "payments",
                "operation": "charge",
                "kind": "server_error",
                "trigger": {"calls": [2]},
            }
        ],
    )
    result = run(s, reference_agent("naive"))
    assert not result.valid
    assert result.invalid_reasons == ["fault payments.charge (server_error) never fired"]
    assert run_state(result.to_dict()) == "invalid"


def test_declared_crash_that_never_happens_is_invalid() -> None:
    # The agent fails honestly at step 1, so the crash after step 1 never comes.
    s = scenario(
        faults=[
            {
                "service": "payments",
                "operation": "charge",
                "kind": "server_error",
                "trigger": {"always": True},
            }
        ],
        crash={"after_step": 1},
    )
    result = run(s, reference_agent("careful"))
    assert "declared crash after step 1 never happened" in result.invalid_reasons


def test_invalid_runs_are_neither_passes_nor_failures() -> None:
    runs = [
        {"agent": "a", "category": "c", "passed": True, "valid": True},
        {"agent": "a", "category": "c", "passed": True, "valid": False},
        {"agent": "a", "category": "c", "passed": False},
    ]
    summary = summarize(runs)
    assert (summary["passed"], summary["failed"], summary["invalid"]) == (1, 1, 1)
    assert summary["by_agent"]["a"] == {"passed": 1, "failed": 1, "invalid": 1}


def test_html_marks_invalid_runs_and_says_why() -> None:
    s = scenario(
        faults=[
            {
                "service": "payments",
                "operation": "charge",
                "kind": "server_error",
                "trigger": {"calls": [2]},
            }
        ],
    )
    record = run_record(run(s, reference_agent("naive")))
    page = render_html(build_results([s], ["naive"], [record], "2026-11-18T00:00:00Z"))
    assert "INVALID" in page
    assert "never fired" in page
    assert "Not counted." in page

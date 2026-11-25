"""The optional Opik reporter: one trace per run, spans, scores."""

from __future__ import annotations

import sys
from typing import Any

import pytest

from agentpostmortem.agents import reference_agent
from agentpostmortem.cli import build_parser
from agentpostmortem.domain.scenario import Scenario
from agentpostmortem.report.opik import (
    FAULT_TAG,
    PASSED_SCORE,
    VALID_SCORE,
    OpikReporter,
    OpikReportError,
    report_to_opik,
    run_tags,
    score_reason,
    trace_name,
)
from agentpostmortem.report.results import build_results, run_record
from agentpostmortem.runner import run


class FakeSpan:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.ended = False

    def end(self) -> None:
        self.ended = True


class FakeTrace:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.spans: list[FakeSpan] = []
        self.scores: list[dict[str, Any]] = []
        self.ended = False

    def span(self, **kwargs: Any) -> FakeSpan:
        span = FakeSpan(**kwargs)
        self.spans.append(span)
        return span

    def log_feedback_score(self, *, name: str, value: float, reason: str | None = None) -> None:
        self.scores.append({"name": name, "value": value, "reason": reason})

    def end(self) -> None:
        self.ended = True


class FakeClient:
    def __init__(self) -> None:
        self.traces: list[FakeTrace] = []
        self.flushes = 0

    def trace(self, **kwargs: Any) -> FakeTrace:
        trace = FakeTrace(**kwargs)
        self.traces.append(trace)
        return trace

    def flush(self, timeout: int | None = None) -> bool:
        self.flushes += 1
        return True


def results_for(
    scenario: Scenario, agent: str, generated_at: str = "2026-11-22T00:00:00Z"
) -> dict[str, Any]:
    record = run_record(run(scenario, reference_agent(agent)))
    return build_results([scenario], [agent], [record], generated_at)


def test_one_trace_per_run_with_scenario_name(scenarios: dict[str, Scenario]) -> None:
    results = results_for(scenarios["APM-S001"], "retrying")
    client = FakeClient()

    count = report_to_opik(results, "apm-demo", client=client)

    assert count == 1
    assert len(client.traces) == 1
    trace = client.traces[0]
    assert trace.kwargs["name"] == "apm:APM-S001"
    assert trace.kwargs["project_name"] == "apm-demo"
    assert trace.kwargs["input"]["scenario"] == "APM-S001"
    assert trace.kwargs["output"]["status"] == "completed"
    assert client.flushes == 1
    assert trace.ended is True


def test_tool_calls_become_tool_spans(scenarios: dict[str, Scenario]) -> None:
    results = results_for(scenarios["APM-S001"], "retrying")
    client = FakeClient()

    report_to_opik(results, "apm-demo", client=client)

    spans = client.traces[0].spans
    tool_spans = [span for span in spans if span.kwargs["type"] == "tool"]
    calls = results["runs"][0]["calls"]
    assert len(tool_spans) == len(calls)
    assert tool_spans[0].kwargs["name"] == f"{calls[0]['service']}.{calls[0]['operation']}"
    assert tool_spans[0].kwargs["output"]["result"] == calls[0]["result"]
    assert all(span.ended for span in tool_spans)


def test_faults_become_tagged_spans(scenarios: dict[str, Scenario]) -> None:
    results = results_for(scenarios["APM-S001"], "retrying")
    client = FakeClient()

    report_to_opik(results, "apm-demo", client=client)

    fault_spans = [s for s in client.traces[0].spans if FAULT_TAG in (s.kwargs.get("tags") or [])]
    faults = results["runs"][0]["faults"]
    assert len(fault_spans) == len(faults)
    assert fault_spans[0].kwargs["name"].startswith("fault:")
    assert fault_spans[0].kwargs["metadata"]["fired"] == faults[0]["fired"]


def test_failed_run_scores_zero_and_passed_run_scores_one(
    scenarios: dict[str, Scenario],
) -> None:
    failing = results_for(scenarios["APM-S001"], "retrying")
    passing = results_for(scenarios["APM-S001"], "idempotent")
    client = FakeClient()

    report_to_opik(failing, "apm-demo", client=client)
    report_to_opik(passing, "apm-demo", client=client)

    fail_scores = {s["name"]: s["value"] for s in client.traces[0].scores}
    pass_scores = {s["name"]: s["value"] for s in client.traces[1].scores}
    assert fail_scores == {PASSED_SCORE: 0.0, VALID_SCORE: 1.0}
    assert pass_scores == {PASSED_SCORE: 1.0, VALID_SCORE: 1.0}


def test_invalid_run_marks_the_valid_score_zero() -> None:
    run_dict = {
        "scenario": "APM-S900",
        "title": "Never fired",
        "category": "duplicate_effects",
        "agent": "example",
        "passed": False,
        "valid": False,
        "invalid_reasons": ["fault payments.refund (timeout_after_success) never fired"],
        "faults": [],
        "outcome": {"status": "completed", "claim": "done"},
        "calls": [],
        "ledger": [],
        "verdicts": [],
        "restarts": 0,
        "meta": {},
    }
    results = {"runs": [run_dict]}
    client = FakeClient()

    report_to_opik(results, "apm-demo", client=client)

    scores = {s["name"]: s for s in client.traces[0].scores}
    assert scores[VALID_SCORE]["value"] == 0.0
    assert "never fired" in scores[VALID_SCORE]["reason"]
    assert "invalid" in run_tags(run_dict)
    assert "invalid" in score_reason(run_dict)


def test_missing_opik_package_raises_a_clear_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "opik", None)  # makes `import opik` raise ImportError

    with pytest.raises(OpikReportError, match="agentpostmortem-bench\\[opik\\]"):
        OpikReporter("apm-demo")


def test_reporter_rejects_an_empty_project_name() -> None:
    with pytest.raises(ValueError, match="project_name"):
        OpikReporter("", client=FakeClient())


def test_trace_name_is_stable() -> None:
    assert trace_name({"scenario": "APM-S005"}) == "apm:APM-S005"


def test_cli_accepts_the_opik_flag() -> None:
    args = build_parser().parse_args(["run", "--opik-project", "apm-demo"])
    assert args.opik_project == "apm-demo"

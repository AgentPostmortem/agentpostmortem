"""The results document and the HTML report."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from agentpostmortem.agents import reference_agent
from agentpostmortem.domain.scenario import Scenario
from agentpostmortem.report.html import render_html
from agentpostmortem.report.results import (
    SCHEMA_VERSION,
    ResultsError,
    build_results,
    dumps,
    load_results,
    resolve_generated_at,
    run_record,
    summarize,
)
from agentpostmortem.runner import run

HOSTILE = '<script>alert("x")</script>'


def hostile_results() -> dict[str, Any]:
    run_dict: dict[str, Any] = {
        "scenario": "APM-S900",
        "title": f"Title {HOSTILE}",
        "category": "duplicate_effects",
        "agent": f"agent{HOSTILE}",
        "passed": False,
        "task": f"Task {HOSTILE}",
        "incident": {"pattern": HOSTILE, "summary": HOSTILE, "references": [HOSTILE]},
        "outcome": {"status": "completed", "claim": HOSTILE, "completed_steps": [HOSTILE]},
        "restarts": 0,
        "verdicts": [
            {"oracle": HOSTILE, "passed": False, "message": HOSTILE, "evidence": [HOSTILE]}
        ],
        "calls": [
            {
                "seq": 1,
                "step": HOSTILE,
                "service": "payments",
                "operation": HOSTILE,
                "attempt": 1,
                "result": HOSTILE,
                "tick": 1,
            }
        ],
        "ledger": [
            {
                "seq": 1,
                "service": "payments",
                "operation": "charge",
                "subject": HOSTILE,
                "payload": {"note": HOSTILE},
                "idempotency_key": HOSTILE,
                "applied": False,
                "tick": 1,
            }
        ],
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "tool": {"name": "agentpostmortem", "version": HOSTILE},
        "generated_at": HOSTILE,
        "summary": summarize([run_dict]),
        "runs": [run_dict],
    }


@pytest.fixture(scope="module")
def results(scenarios: dict[str, Scenario]) -> dict[str, Any]:
    chosen = [scenarios["APM-S001"], scenarios["APM-S004"]]
    agents = ["naive", "idempotent"]
    runs = [run_record(run(s, reference_agent(a))) for s in chosen for a in agents]
    return build_results(chosen, agents, runs, "2026-01-01T00:00:00Z")


def test_run_record_carries_incident_and_task(scenarios: dict[str, Scenario]) -> None:
    scenario = scenarios["APM-S001"]
    record = run_record(run(scenario, reference_agent("naive")))
    assert record["incident"]["summary"] == scenario.incident.summary
    assert record["task"] == scenario.task


def test_summary_counts(results: dict[str, Any]) -> None:
    summary = results["summary"]
    assert summary["total"] == 4
    assert summary["passed"] + summary["failed"] == 4
    assert set(summary["by_agent"]) == {"naive", "idempotent"}
    assert sum(c["passed"] + c["failed"] for c in summary["by_category"].values()) == 4


def test_results_round_trip(tmp_path: Path, results: dict[str, Any]) -> None:
    path = tmp_path / "results.json"
    path.write_text(dumps(results), encoding="utf-8")
    assert load_results(path) == json.loads(dumps(results))


def test_newer_schema_is_rejected(tmp_path: Path, results: dict[str, Any]) -> None:
    path = tmp_path / "results.json"
    path.write_text(json.dumps({**results, "schema_version": SCHEMA_VERSION + 1}), "utf-8")
    with pytest.raises(ResultsError, match="schema_version"):
        load_results(path)


def test_generated_at_resolution(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APM_GENERATED_AT", "pinned")
    assert resolve_generated_at("explicit") == "explicit"
    assert resolve_generated_at() == "pinned"
    monkeypatch.delenv("APM_GENERATED_AT")
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", resolve_generated_at())


def test_html_contains_summary_matrix_and_runs(results: dict[str, Any]) -> None:
    page = render_html(results)
    assert page.startswith("<!doctype html>")
    assert '<table class="matrix">' in page
    matrix = page.split('<table class="matrix">', 1)[1].split("</table>", 1)[0]
    assert matrix.count('<th scope="row">') == 2
    for agent in ("naive", "idempotent"):
        assert agent in matrix
    assert 'href="#run-apm-s004-naive"' in matrix
    assert page.count('<details class="run') == 4
    assert 'id="run-apm-s004-naive"' in page
    assert "By agent" in page and "By category" in page


def test_html_marks_status_with_text_not_only_colour(results: dict[str, Any]) -> None:
    page = render_html(results)
    assert ">PASS</span>" in page and ">FAIL</span>" in page
    assert ">Deduplicated</span>" in page, "idempotent agent dedupes the retried refund"
    assert ">Applied</span>" in page


def test_html_is_self_contained(results: dict[str, Any]) -> None:
    page = render_html(results)
    assert "<link" not in page
    assert not re.search(r"""\ssrc=["']""", page)
    assert "@import" not in page
    assert "url(" not in page


def test_html_supports_dark_mode_and_focus(results: dict[str, Any]) -> None:
    page = render_html(results)
    assert "prefers-color-scheme:dark" in page
    assert ":focus-visible" in page
    assert 'scope="col"' in page and 'scope="row"' in page


def test_html_escapes_hostile_strings() -> None:
    page = render_html(hostile_results())
    assert HOSTILE not in page
    assert "<script>alert" not in page
    assert "&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt;" in page
    # The only script element is the report's own progressive enhancement.
    assert page.count("<script>") == 1


def test_html_handles_minimal_documents() -> None:
    page = render_html({"schema_version": 1, "summary": {}, "runs": []})
    assert "no runs" in page


def test_html_is_deterministic(results: dict[str, Any]) -> None:
    assert render_html(results) == render_html(json.loads(dumps(results)))

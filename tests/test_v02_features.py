"""Tests for v0.2.0 features: Files, Webhooks, late_commit, silent_error, JUnit, Model mode."""

from __future__ import annotations

import json
import threading
import xml.etree.ElementTree as ET
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from agentpostmortem.agents import ModelAgent, ModelConfigError, ModelOptions, reference_agent
from agentpostmortem.cli import EXIT_OK, EXIT_USAGE, main
from agentpostmortem.domain.faults import FaultKind, FaultSpec, Trigger
from agentpostmortem.domain.scenario import Scenario, load_scenarios
from agentpostmortem.ledger import Ledger
from agentpostmortem.report.junit import render_junit
from agentpostmortem.report.results import GENERATED_AT_ENV, summarize_reliability, wilson_interval
from agentpostmortem.runner import run
from agentpostmortem.sims import Files, Webhooks
from agentpostmortem.sims.errors import Conflict, InvalidRequest, NotFound, ToolTimeout

SCENARIOS = Path(__file__).resolve().parents[1] / "scenarios"


def test_files_service_writes_appends_and_partial_patches() -> None:
    ledger = Ledger()
    faults = [
        FaultSpec(
            service="files", operation="read", kind=FaultKind.STALE, trigger=Trigger(calls=(1,))
        ),
        FaultSpec(
            service="files",
            operation="apply_patch_batch",
            kind=FaultKind.PARTIAL,
            trigger=Trigger(calls=(1,)),
            params={"paths": ["b.py"]},
        ),
    ]
    files = Files(ledger, faults)
    files.seed({"contents": {"a.cfg": "v=2"}, "history": {"a.cfg": ["v=1"]}})

    stale_read = files.call("read", path="a.cfg")
    assert stale_read["content"] == "v=1"
    with pytest.raises(Conflict):
        files.call("write", path="a.cfg", content="v=3", expected_sha=stale_read["sha"])

    live = files.call("read", path="a.cfg")
    first = files.call(
        "write", path="a.cfg", content="v=3", expected_sha=live["sha"], idempotency_key="k1"
    )
    replay = files.call(
        "write", path="a.cfg", content="v=3", expected_sha=live["sha"], idempotency_key="k1"
    )
    assert first == replay
    assert [e.applied for e in ledger.applied("files", "write")] == [True]

    files.call("append", path="log.txt", line="entry1", idempotency_key="ignored")
    files.call("append", path="log.txt", line="entry1", idempotency_key="ignored")
    assert len(ledger.applied("files", "append")) == 2

    patch = files.call(
        "apply_patch_batch",
        changes=[{"path": "a.py", "content": "x=1"}, {"path": "b.py", "content": "y=2"}],
        idempotency_key="patch-1",
    )
    assert patch == {"status": "partial", "applied": 1, "failed": ["b.py"]}
    assert (
        files.call(
            "apply_patch_batch",
            changes=[{"path": "a.py", "content": "x=1"}, {"path": "b.py", "content": "y=2"}],
            idempotency_key="patch-1",
        )
        == patch
    )

    with pytest.raises(NotFound):
        files.call("read", path="missing.txt")


def test_webhooks_service_and_mixed_idempotency() -> None:
    ledger = Ledger()
    verify_fault = FaultSpec(
        service="webhooks", operation="verify", kind=FaultKind.STALE, trigger=Trigger(calls=(1,))
    )
    hooks = Webhooks(ledger, [verify_fault])
    first = hooks.call(
        "deliver", target="https://hooks.example/o", event="order.paid", idempotency_key="hk1"
    )
    again = hooks.call(
        "deliver", target="https://hooks.example/o", event="order.paid", idempotency_key="hk1"
    )
    assert first == again
    assert len(ledger.applied("webhooks", "deliver")) == 1

    hooks.call("notify", target="https://hooks.example/o", event="legacy", idempotency_key="k1")
    hooks.call("notify", target="https://hooks.example/o", event="legacy", idempotency_key="k1")
    assert len(ledger.applied("webhooks", "notify")) == 2

    assert hooks.call("verify", delivery_id=first["id"])["status"] == "pending"
    assert hooks.call("verify", delivery_id=first["id"])["status"] == "succeeded"

    with pytest.raises(InvalidRequest):
        hooks.call("deliver", target="ftp://bad", event="x")


def test_late_commit_schedules_deferred_write_and_deduplicates_with_key() -> None:
    ledger = Ledger()
    fault = FaultSpec(
        service="webhooks",
        operation="deliver",
        kind=FaultKind.LATE_COMMIT,
        trigger=Trigger(calls=(1,)),
        params={"delay": 2},
    )
    hooks = Webhooks(ledger, [fault])

    with pytest.raises(ToolTimeout):
        hooks.call("deliver", target="https://hooks.example/a", event="ev", idempotency_key="k1")
    assert len(ledger.applied("webhooks", "deliver")) == 0

    hooks.call("deliver", target="https://hooks.example/a", event="ev", idempotency_key="k1")
    assert len(ledger.applied("webhooks", "deliver")) == 1

    ledger.clock.flush_pending()
    assert len(ledger.applied("webhooks", "deliver")) == 1
    assert [e.applied for e in ledger] == [True, False]


def test_late_commit_without_key_creates_duplicate_effects() -> None:
    ledger = Ledger()
    fault = FaultSpec(
        service="webhooks",
        operation="deliver",
        kind=FaultKind.LATE_COMMIT,
        trigger=Trigger(calls=(1,)),
        params={"delay": 2},
    )
    hooks = Webhooks(ledger, [fault])
    with pytest.raises(ToolTimeout):
        hooks.call("deliver", target="https://hooks.example/a", event="ev")
    hooks.call("deliver", target="https://hooks.example/a", event="ev")
    ledger.clock.flush_pending()
    assert len(ledger.applied("webhooks", "deliver")) == 2


def test_silent_error_caught_by_careful_missed_by_naive() -> None:
    scenarios = {s.id: s for s in load_scenarios(SCENARIOS)}
    scenario = scenarios["APM-S030"]
    assert not run(scenario, reference_agent("naive")).passed
    assert not run(scenario, reference_agent("idempotent")).passed
    assert run(scenario, reference_agent("careful")).passed


def test_junit_render_and_cli_flag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(GENERATED_AT_ENV, "2026-01-01T00:00:00Z")
    junit_path = tmp_path / "junit.xml"
    code = main(
        [
            "run",
            "--scenarios",
            str(SCENARIOS),
            "--scenario",
            "APM-S003",
            "--agent",
            "naive",
            "careful",
            "--out",
            str(tmp_path),
            "--junit",
            str(junit_path),
        ]
    )
    assert code == EXIT_OK
    root = ET.fromstring(junit_path.read_text(encoding="utf-8"))
    assert root.tag == "testsuites"
    assert root.attrib["tests"] == "2"
    assert root.attrib["failures"] == "1"
    suite = root.find("testsuite")
    assert suite is not None
    cases = suite.findall("testcase")
    assert len(cases) == 2
    assert cases[0].find("failure") is not None
    assert cases[1].find("failure") is None

    xml_with_invalid = render_junit(
        {
            "generated_at": "2026-01-01T00:00:00Z",
            "runs": [
                {
                    "scenario": "APM-S999",
                    "title": "<bad&title>",
                    "category": "compound",
                    "agent": "x",
                    "passed": False,
                    "valid": False,
                    "invalid_reasons": ["fault never fired"],
                    "verdicts": [],
                }
            ],
        }
    )
    parsed = ET.fromstring(xml_with_invalid)
    assert parsed.attrib["skipped"] == "1"


def test_model_agent_completes_scenario_over_openai_compatible_http(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(GENERATED_AT_ENV, "2026-01-01T00:00:00Z")
    turns: list[dict[str, Any]] = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            return

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length).decode("utf-8"))
            turns.append(body)
            step = sum(1 for m in body.get("messages", []) if m.get("role") == "tool") + 1
            if step == 1:
                msg: dict[str, Any] = {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {
                                "name": "payments_refund",
                                "arguments": json.dumps(
                                    {
                                        "charge": "ch_0001",
                                        "amount": 2000,
                                        "idempotency_key": "k_refund_1",
                                    }
                                ),
                            },
                        }
                    ],
                }
            elif step == 2:
                msg = {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call_2",
                            "type": "function",
                            "function": {
                                "name": "payments_refund",
                                "arguments": json.dumps(
                                    {
                                        "charge": "ch_0001",
                                        "amount": 2000,
                                        "idempotency_key": "k_refund_1",
                                    }
                                ),
                            },
                        }
                    ],
                }
            else:
                msg = {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call_3",
                            "type": "function",
                            "function": {
                                "name": "finish",
                                "arguments": json.dumps(
                                    {
                                        "status": "completed",
                                        "message": "Refunded ch_0001 once safely.",
                                    }
                                ),
                            },
                        }
                    ],
                }
            resp = json.dumps({"choices": [{"message": msg}]}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(resp)))
            self.end_headers()
            self.wfile.write(resp)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        code = main(
            [
                "run",
                "--scenarios",
                str(SCENARIOS),
                "--scenario",
                "APM-S001",
                "--model",
                "mock-model",
                "--base-url",
                f"http://127.0.0.1:{port}/v1",
                "--trials",
                "3",
                "--out",
                str(tmp_path),
            ]
        )
        assert code == EXIT_OK
    finally:
        server.shutdown()
        server.server_close()

    results = json.loads((tmp_path / "results.json").read_text(encoding="utf-8"))
    assert results["agents"] == ["model:mock-model"]
    assert results["summary"]["total"] == 3
    assert results["summary"]["passed"] == 3
    reliability = results["summary"]["reliability"]["model:mock-model"]
    assert reliability["trials"] == 3
    assert reliability["pass_at_1"] == 1.0
    assert reliability["pass_k"] == 1.0


def test_model_mode_requires_key_for_remote_host() -> None:
    scenarios = {s.id: s for s in load_scenarios(SCENARIOS)}
    agent = ModelAgent(ModelOptions(model="gpt-4o", api_key_env="MISSING_APM_TEST_KEY"))
    with pytest.raises(ModelConfigError, match="MISSING_APM_TEST_KEY"):
        run(scenarios["APM-S001"], agent)


def test_trials_validation_and_wilson_math(tmp_path: Path) -> None:
    assert (
        main(
            [
                "run",
                "--scenarios",
                str(SCENARIOS),
                "--trials",
                "0",
                "--out",
                str(tmp_path),
            ]
        )
        == EXIT_USAGE
    )
    low, high = wilson_interval(8, 10)
    assert 0.45 < low < 0.8 < high < 1.0
    assert wilson_interval(0, 0) == [0.0, 0.0]
    rel = summarize_reliability(
        [
            {"agent": "m", "scenario": "S1", "passed": True, "valid": True},
            {"agent": "m", "scenario": "S1", "passed": False, "valid": True},
            {"agent": "m", "scenario": "S2", "passed": True, "valid": True},
            {"agent": "m", "scenario": "S2", "passed": True, "valid": True},
        ],
        trials=2,
    )
    assert rel["m"]["pass_at_1"] == 0.75
    assert rel["m"]["pass_k"] == 0.5


def test_model_agent_only_crashes_after_ok_step_and_handles_bad_json() -> None:
    scenario = Scenario.model_validate(
        {
            "id": "APM-S990",
            "title": "Crash after step 1 only after step succeeds",
            "category": "progress_and_resume",
            "incident": {"pattern": "p", "summary": "s"},
            "task": "Charge cus_ada 5.00 and email receipt.",
            "services": ["payments", "email"],
            "plan": [
                {
                    "id": "charge",
                    "service": "payments",
                    "operation": "charge",
                    "args": {"customer": "cus_ada", "amount": 500},
                },
                {
                    "id": "receipt",
                    "service": "email",
                    "operation": "send",
                    "args": {"to": "ada@example.com", "subject": "Receipt"},
                },
            ],
            "faults": [
                {
                    "service": "payments",
                    "operation": "charge",
                    "kind": "server_error",
                    "trigger": {"calls": [1]},
                }
            ],
            "crash": {"after_step": 1},
            "oracles": [
                {"name": "no_duplicate_effects"},
                {
                    "name": "effect_completed",
                    "params": {"service": "email", "operation": "send", "count": 1},
                },
            ],
        }
    )
    charge_args = json.dumps({"customer": "cus_ada", "amount": 500})
    keyed_args = json.dumps({"customer": "cus_ada", "amount": 500, "idempotency_key": "k"})
    mail_args = json.dumps({"to": "ada@example.com", "subject": "Receipt", "idempotency_key": "m"})
    replies = iter(
        [
            {
                "choices": [
                    {
                        "message": {
                            "tool_calls": [
                                {
                                    "id": "c1",
                                    "function": {"name": "payments_charge", "arguments": "{bad"},
                                }
                            ]
                        }
                    }
                ]
            },
            {
                "choices": [
                    {
                        "message": {
                            "tool_calls": [
                                {
                                    "id": "c2",
                                    "function": {
                                        "name": "payments_charge",
                                        "arguments": charge_args,
                                    },
                                }
                            ]
                        }
                    }
                ]
            },
            {
                "choices": [
                    {
                        "message": {
                            "tool_calls": [
                                {
                                    "id": "c3",
                                    "function": {
                                        "name": "payments_charge",
                                        "arguments": keyed_args,
                                    },
                                }
                            ]
                        }
                    }
                ]
            },
            # Post-crash fresh process retries step 1 with same key, sends receipt, and finishes.
            {
                "choices": [
                    {
                        "message": {
                            "tool_calls": [
                                {
                                    "id": "c4",
                                    "function": {
                                        "name": "payments_charge",
                                        "arguments": keyed_args,
                                    },
                                }
                            ]
                        }
                    }
                ]
            },
            {
                "choices": [
                    {
                        "message": {
                            "tool_calls": [
                                {
                                    "id": "c5",
                                    "function": {"name": "email_send", "arguments": mail_args},
                                }
                            ]
                        }
                    }
                ]
            },
            {
                "choices": [
                    {
                        "message": {
                            "tool_calls": [
                                {
                                    "id": "c6",
                                    "function": {
                                        "name": "finish",
                                        "arguments": '{"status":"completed","message":"Done"}',
                                    },
                                }
                            ]
                        }
                    }
                ]
            },
        ]
    )
    result = run(
        scenario, ModelAgent(ModelOptions(model="m"), completion_fn=lambda _: next(replies))
    )
    assert result.restarts == 1
    assert result.passed
    assert result.valid


def test_files_patch_batch_rejects_bad_item_and_clock_fast_forwards() -> None:
    ledger = Ledger()
    fault = FaultSpec(
        service="files",
        operation="write",
        kind=FaultKind.LATE_COMMIT,
        trigger=Trigger(calls=(1,)),
        params={"delay": 2},
    )
    files = Files(ledger, [fault])
    with pytest.raises(InvalidRequest):
        files.call("apply_patch_batch", changes=["not-a-dict"])
    with pytest.raises(ToolTimeout):
        files.call("write", path="a.txt", content="hello", idempotency_key="fk1")
    ledger.clock.tick(10_000)
    assert ledger.clock.now == 10_002
    writes = ledger.applied("files", "write")
    assert len(writes) == 1
    assert writes[0].tick == 4

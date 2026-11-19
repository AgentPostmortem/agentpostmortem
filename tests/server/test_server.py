"""Serving a scenario to an external agent: the session, the HTTP front and ``apm serve``.

The central claim is parity: an external agent that makes the same calls as a
reference agent gets the same ledger, the same verdicts and the same validity
as ``apm run`` gives that reference agent in-process.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast

import pytest

from agentpostmortem.agents import REFERENCE_AGENTS, reference_agent
from agentpostmortem.agents.base import CheckpointStore
from agentpostmortem.cli import EXIT_USAGE, main
from agentpostmortem.domain.outcome import Status
from agentpostmortem.domain.scenario import Scenario
from agentpostmortem.report.results import GENERATED_AT_ENV, load_results
from agentpostmortem.runner import RunResult, run
from agentpostmortem.server import (
    CRASH_NOT_SUPPORTED,
    NO_FINISH,
    ReportWriter,
    Session,
    SessionError,
)
from agentpostmortem.server.http_front import LOOPBACK, SessionHTTPServer, start_in_thread
from agentpostmortem.sims import errors
from agentpostmortem.toolbox import Toolbox

SCENARIOS = Path(__file__).resolve().parents[2] / "scenarios"
PINNED = "2026-01-01T00:00:00Z"
_ERRORS: dict[str, type[errors.ToolError]] = {
    cls.code: cls
    for cls in (
        errors.ToolTimeout,
        errors.ServerError,
        errors.NotFound,
        errors.InvalidRequest,
        errors.Conflict,
    )
}


# A reference agent driving a session the way an external process would --------


class _RemoteClock:
    def __init__(self, session: Session) -> None:
        self._session = session

    def tick(self, amount: int = 1) -> int:
        body = self._session.call("wait", {"ticks": amount}).body
        return int(body["now"])


class _RemoteLedger:
    def __init__(self, session: Session) -> None:
        self.clock = _RemoteClock(session)


class RemoteTools:
    """Looks like a Toolbox to a scripted agent, but only talks to the session's tools.

    It sees what an external agent sees: tool names, JSON arguments, JSON results
    and error codes. It never tells the session which plan step it means; the
    session works that out, and ``mismatches`` records any disagreement.
    """

    def __init__(self, session: Session) -> None:
        self.session = session
        self.ledger = _RemoteLedger(session)
        self.mismatches: list[tuple[str, str | None]] = []

    def call(self, step: str, service: str, operation: str, **kwargs: Any) -> dict[str, Any]:
        result = self.session.call(f"{service}_{operation}", kwargs)
        if result.step != step:
            self.mismatches.append((step, result.step))
        if result.ok:
            return result.body
        code = result.body["error"]
        if code == "rate_limit":
            raise errors.RateLimited(int(result.body["retry_after"]))
        raise _ERRORS.get(code, errors.ToolError)(result.body["message"])

    def finish_step(self, step: str) -> None:
        """An external process cannot be killed by the harness, so no crash here."""


def drive(scenario: Scenario, agent: str) -> tuple[Session, RemoteTools]:
    session = Session(scenario, agent_name=agent)
    tools = RemoteTools(session)
    outcome = reference_agent(agent).run(scenario, cast(Toolbox, tools), CheckpointStore())
    session.call(
        "finish",
        {
            "status": outcome.status.value,
            "message": outcome.claim,
            "completed_steps": list(outcome.completed_steps),
        },
    )
    return session, tools


def comparable(result: RunResult) -> dict[str, Any]:
    record = result.to_dict()
    for key in ("agent", "meta"):
        record.pop(key)
    return record


NON_CRASH = sorted(
    path.name[:8] for path in SCENARIOS.glob("*.yaml") if Scenario.from_yaml(path).crash is None
)
CRASH = sorted(
    path.name[:8] for path in SCENARIOS.glob("*.yaml") if Scenario.from_yaml(path).crash is not None
)


@pytest.mark.parametrize("agent", list(REFERENCE_AGENTS))
@pytest.mark.parametrize("scenario_id", NON_CRASH)
def test_session_matches_apm_run(
    scenarios: dict[str, Scenario], scenario_id: str, agent: str
) -> None:
    scenario = scenarios[scenario_id]
    expected = run(scenario, reference_agent(agent))
    session, tools = drive(scenario, agent)
    got = session.result()
    assert tools.mismatches == []
    assert comparable(got) == comparable(expected)
    assert got.meta == {"transport": "session"}


def test_crash_scenarios_are_reported_invalid(scenarios: dict[str, Scenario]) -> None:
    assert CRASH, "the catalogue should hold crash scenarios"
    for scenario_id in CRASH:
        session, _ = drive(scenarios[scenario_id], "careful")
        result = session.result()
        assert not result.valid
        assert result.invalid_reasons[0] == CRASH_NOT_SUPPORTED
        assert any("declared crash" in r for r in result.invalid_reasons)
        assert CRASH_NOT_SUPPORTED in session.instructions


# The session's own surface -----------------------------------------------------


def test_tools_come_from_the_plan(scenarios: dict[str, Scenario]) -> None:
    session = Session(scenarios["APM-S001"])
    tools = {tool.name: tool for tool in session.list_tools()}
    assert set(tools) == {"payments_refund", "wait", "finish"}
    schema = tools["payments_refund"].input_schema
    assert schema["required"] == ["charge", "amount"]
    assert schema["properties"]["amount"] == {"type": "integer"}
    assert schema["properties"]["idempotency_key"]["type"] == "string"
    assert schema["additionalProperties"] is False
    assert "Refund 20.00 of charge ch_0001" in session.instructions
    assert 'payments_refund(charge="ch_0001", amount=2000)' in session.instructions


def test_list_arguments_get_an_array_schema() -> None:
    scenario = Scenario.model_validate(
        {
            "id": "APM-S901",
            "title": "List args",
            "category": "duplicate_effects",
            "incident": {"pattern": "p", "summary": "s"},
            "task": "Store the tags.",
            "services": ["kv"],
            "plan": [
                {
                    "id": "tags",
                    "service": "kv",
                    "operation": "put",
                    "args": {"key": "tags", "value": ["a", "b"]},
                }
            ],
            "oracles": [{"name": "no_duplicate_effects"}],
        }
    )
    schema = Session(scenario).tools["kv_put"].input_schema
    assert schema["properties"]["value"] == {"type": "array", "items": {"type": "string"}}


def test_faults_fire_and_the_ledger_records_effects(scenarios: dict[str, Scenario]) -> None:
    session = Session(scenarios["APM-S001"])
    first = session.call("payments_refund", {"charge": "ch_0001", "amount": 2000})
    assert not first.ok
    assert first.body["error"] == "timeout"
    assert first.step == "refund"
    assert len(session.ledger.applied("payments", "refund")) == 1
    second = session.call("payments_refund", {"charge": "ch_0001", "amount": 2000})
    assert second.ok
    assert len(session.ledger.applied("payments", "refund")) == 2
    session.call("finish", {"status": "completed", "message": "Refunded."})
    result = session.result()
    assert result.valid
    assert not result.passed
    assert [c.attempt for c in result.calls] == [1, 2]


def test_handwritten_calls_match_reference_agents(scenarios: dict[str, Scenario]) -> None:
    # S001 with an idempotency key: the retry is deduplicated, like the idempotent agent.
    session = Session(scenarios["APM-S001"], agent_name="idempotent")
    args = {"charge": "ch_0001", "amount": 2000, "idempotency_key": "refund-v1"}
    assert session.call("payments_refund", args).body["error"] == "timeout"
    assert session.call("payments_refund", args).ok
    session.call("finish", {"status": "completed", "message": "Done.", "completed_steps": []})
    expected = run(scenarios["APM-S001"], reference_agent("idempotent"))
    assert [(v.oracle, v.passed) for v in session.result().verdicts] == [
        (v.oracle, v.passed) for v in expected.verdicts
    ]
    assert session.result().passed and expected.passed

    # S009 naive: the batch partly fails and the agent claims success anyway.
    session = Session(scenarios["APM-S009"], agent_name="naive")
    batch = {"audience": "outage_affected", "subject": "Service disruption on your account"}
    assert session.call("email_send_batch", batch).body["status"] == "partial"
    session.call("finish", {"status": "completed", "message": "Everyone was notified."})
    expected = run(scenarios["APM-S009"], reference_agent("naive"))
    assert [(v.oracle, v.passed) for v in session.result().verdicts] == [
        (v.oracle, v.passed) for v in expected.verdicts
    ]
    assert not session.result().passed


def test_calls_map_to_the_matching_plan_step(scenarios: dict[str, Scenario]) -> None:
    session = Session(scenarios["APM-S014"])
    tool = "helpdesk_create_ticket"
    subject = "Outage follow-up"
    assert session.call(tool, {"customer": "cus_bo", "subject": subject}).step == "ticket_bo"
    assert session.call(tool, {"customer": "cus_ada", "subject": subject}).step == "ticket_ada"
    assert session.call(tool, {"customer": "cus_ada", "subject": subject}).step == "ticket_ada"
    assert session.toolbox.attempts("ticket_ada") == 2


def test_wait_moves_the_clock(scenarios: dict[str, Scenario]) -> None:
    session = Session(scenarios["APM-S019"])
    before = session.ledger.clock.now
    assert session.call("wait", {"ticks": 30}).body == {"waited": 30, "now": before + 30}
    for bad in ({"ticks": 0}, {"ticks": "5"}, {"ticks": 1, "extra": 1}):
        with pytest.raises(SessionError) as info:
            session.call("wait", bad)
        assert info.value.code == "bad_arguments"


def test_bad_requests_never_reach_a_service(scenarios: dict[str, Scenario]) -> None:
    session = Session(scenarios["APM-S001"])
    cases: list[tuple[str, dict[str, Any], str]] = [
        ("nope", {}, "unknown_tool"),
        ("payments_refund", {"charge": "ch_0001"}, "bad_arguments"),
        ("payments_refund", {"charge": "ch_0001", "amount": 1, "x": 1}, "bad_arguments"),
        ("finish", {"status": "maybe", "message": "?"}, "bad_arguments"),
    ]
    for name, args, code in cases:
        with pytest.raises(SessionError) as info:
            session.call(name, args)
        assert info.value.code == code
    assert session.toolbox.calls == []
    assert len(session.ledger) == 0


def test_finish_is_final(scenarios: dict[str, Scenario]) -> None:
    session = Session(scenarios["APM-S001"])
    body = session.call("finish", {"status": "failed", "message": "Could not refund."}).body
    assert body["finished"] is True
    assert body["valid"] is False  # the declared timeout never fired
    assert session.outcome is not None and session.outcome.status is Status.FAILED
    for name, args in (("finish", {"status": "failed", "message": "again"}), ("wait", {})):
        with pytest.raises(SessionError) as info:
            session.call(name, args)
        assert info.value.code == "finished"


def test_a_run_without_finish_is_invalid(scenarios: dict[str, Scenario]) -> None:
    session = Session(scenarios["APM-S001"])
    session.call("payments_refund", {"charge": "ch_0001", "amount": 2000})
    result = session.result()
    assert NO_FINISH in result.invalid_reasons
    assert result.outcome.status is Status.FAILED


def test_reports_are_the_apm_run_files(scenarios: dict[str, Scenario], tmp_path: Path) -> None:
    session = Session(scenarios["APM-S001"], agent_name="my-agent", transport="http")
    writer = ReportWriter(session, tmp_path, PINNED)
    session.call("payments_refund", {"charge": "ch_0001", "amount": 2000})
    assert writer.after_call() is None
    session.call("finish", {"status": "completed", "message": "Done."})
    paths = writer.after_call()
    assert paths == (tmp_path / "results.json", tmp_path / "report.html")
    assert writer.after_call() is None and writer.at_shutdown() is None
    results = load_results(tmp_path / "results.json")
    assert results["generated_at"] == PINNED
    assert results["agents"] == ["my-agent"]
    assert results["summary"]["total"] == 1
    record = results["runs"][0]
    assert record["meta"] == {"transport": "http"}
    assert record["ledger"] == session.ledger.to_list()
    assert "my-agent" in (tmp_path / "report.html").read_text(encoding="utf-8")


# HTTP front ----------------------------------------------------------------------


@pytest.fixture
def http_session(scenarios: dict[str, Scenario]) -> Iterator[tuple[Session, SessionHTTPServer]]:
    session = Session(scenarios["APM-S001"], agent_name="http-agent", transport="http")
    server = SessionHTTPServer(session, 0)
    thread = start_in_thread(server)
    try:
        yield session, server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def request(
    url: str, body: object = None, headers: dict[str, str] | None = None
) -> tuple[int, dict[str, Any]]:
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers or {})
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            return int(response.status), json.loads(response.read())
    except urllib.error.HTTPError as error:
        return int(error.code), json.loads(error.read())


def test_http_round_trip_matches_apm_run(
    http_session: tuple[Session, SessionHTTPServer], scenarios: dict[str, Scenario]
) -> None:
    session, server = http_session
    status, listing = request(f"{server.url}/tools")
    assert status == 200
    assert listing["scenario"] == "APM-S001"
    assert "Task: Refund" in listing["instructions"]
    assert [t["name"] for t in listing["tools"]] == ["payments_refund", "wait", "finish"]

    args = {"charge": "ch_0001", "amount": 2000}
    status, body = request(f"{server.url}/tools/payments_refund", args)
    assert (status, body["ok"], body["error"], body["step"]) == (504, False, "timeout", "refund")
    assert len(session.ledger.applied("payments", "refund")) == 1

    status, body = request(f"{server.url}/tools/payments_refund", args)
    assert (status, body["ok"], body["result"]["status"]) == (200, True, "succeeded")

    status, body = request(f"{server.url}/finish", {"status": "completed", "message": "Done."})
    assert status == 200 and body["result"]["finished"] is True

    status, results = request(f"{server.url}/results")
    assert status == 200
    record = results["runs"][0]
    expected = run(scenarios["APM-S001"], reference_agent("retrying"))
    assert record["agent"] == "http-agent"
    assert record["valid"] is True
    assert record["passed"] is expected.passed is False
    assert [v["passed"] for v in record["verdicts"]] == [v.passed for v in expected.verdicts]
    assert record["ledger"] == expected.ledger.to_list()

    status, body = request(f"{server.url}/tools/payments_refund", args)
    assert (status, body["error"]) == (409, "finished")


def test_http_errors(http_session: tuple[Session, SessionHTTPServer]) -> None:
    _, server = http_session
    assert request(f"{server.url}/tools/nope", {})[0] == 404
    assert request(f"{server.url}/nowhere")[0] == 404
    status, body = request(f"{server.url}/tools/payments_refund", {"charge": "ch_0001"})
    assert (status, body["error"]) == (400, "bad_arguments")
    req = urllib.request.Request(f"{server.url}/finish", data=b"[1", method="POST")
    with pytest.raises(urllib.error.HTTPError) as info:
        urllib.request.urlopen(req, timeout=10)
    assert info.value.code == 400


def test_http_rejects_foreign_hosts_and_origins(
    http_session: tuple[Session, SessionHTTPServer],
) -> None:
    session, server = http_session
    status, _ = request(f"{server.url}/tools", headers={"Host": "evil.example"})
    assert status == 403
    status, _ = request(
        f"{server.url}/tools/payments_refund",
        {"charge": "ch_0001", "amount": 2000},
        headers={"Origin": "https://evil.example"},
    )
    assert status == 403
    assert session.toolbox.calls == []
    assert (
        request(f"{server.url}/tools", headers={"Origin": f"http://localhost:{server.port}"})[0]
        == 200
    )


def _non_loopback_address() -> str | None:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        try:
            probe.connect(("192.0.2.1", 9))  # TEST-NET-1: no packet is sent for UDP connect
        except OSError:
            return None
        address = str(probe.getsockname()[0])
    return None if address.startswith("127.") or address == "0.0.0.0" else address


def test_http_binds_to_loopback_only(http_session: tuple[Session, SessionHTTPServer]) -> None:
    _, server = http_session
    assert server.server_address[0] == LOOPBACK == "127.0.0.1"
    address = _non_loopback_address()
    if address is None:
        pytest.skip("no non-loopback interface to probe")
    with pytest.raises(OSError), socket.create_connection((address, server.port), timeout=2):
        pass


# apm serve -------------------------------------------------------------------------


def test_serve_rejects_unknown_scenarios(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["serve", "--scenario", "APM-S999", "--scenarios", str(SCENARIOS)])
    assert code == EXIT_USAGE
    assert "unknown scenario APM-S999" in capsys.readouterr().err


def test_apm_serve_http_end_to_end(tmp_path: Path) -> None:
    out = tmp_path / "out"
    command = [
        sys.executable,
        "-m",
        "agentpostmortem.cli",
        "serve",
        "--scenario",
        "APM-S001",
        "--transport",
        "http",
        "--port",
        "0",
        "--exit-after-finish",
        "--agent-name",
        "cli-agent",
        "--out",
        str(out),
        "--scenarios",
        str(SCENARIOS),
    ]
    env = {**_env(), GENERATED_AT_ENV: PINNED}
    with subprocess.Popen(command, stderr=subprocess.PIPE, text=True, env=env) as process:
        try:
            assert process.stderr is not None
            line = process.stderr.readline()
            assert "over HTTP at http://127.0.0.1:" in line, line
            url = line.rsplit(" ", 1)[-1].strip()
            args = {"charge": "ch_0001", "amount": 2000, "idempotency_key": "k1"}
            assert request(f"{url}/tools/payments_refund", args)[0] == 504
            assert request(f"{url}/tools/payments_refund", args)[0] == 200
            assert request(f"{url}/finish", {"status": "completed", "message": "ok"})[0] == 200
            assert process.wait(timeout=20) == 0
            tail = process.stderr.read()
        finally:
            process.kill()
    assert "wrote" in tail
    results = load_results(out / "results.json")
    assert results["agents"] == ["cli-agent"]
    assert results["generated_at"] == PINNED
    assert results["runs"][0]["passed"] is True
    assert results["runs"][0]["valid"] is True
    assert (out / "report.html").is_file()


def _env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if k != "APM_SCENARIOS"}

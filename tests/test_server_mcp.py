"""The MCP server, driven with the official SDK client in memory and over stdio."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import anyio
import pytest

pytest.importorskip("mcp")

from mcp import Client, StdioServerParameters

from agentpostmortem.agents import reference_agent
from agentpostmortem.domain.scenario import Scenario
from agentpostmortem.report.results import GENERATED_AT_ENV, load_results
from agentpostmortem.runner import run
from agentpostmortem.server import Session
from agentpostmortem.server.mcp_server import build_server

SCENARIOS = Path(__file__).resolve().parents[1] / "scenarios"
PINNED = "2026-01-01T00:00:00Z"
TIMEOUT = 30


def _text(result: Any) -> dict[str, Any]:
    value = json.loads(result.content[0].text)
    assert isinstance(value, dict)
    return value


def test_mcp_tools_faults_and_verdicts(scenarios: dict[str, Scenario]) -> None:
    session = Session(scenarios["APM-S001"], agent_name="mcp-agent", transport="mcp")
    calls: list[int] = []
    server = build_server(session, lambda: calls.append(1))

    async def scenario() -> None:
        with anyio.fail_after(TIMEOUT):
            async with Client(server) as client:
                assert client.server_info is not None
                assert client.instructions is not None
                assert "Refund 20.00 of charge ch_0001" in client.instructions
                listing = await client.list_tools()
                tools = {t.name: t for t in listing.tools}
                assert set(tools) == {"payments_refund", "wait", "finish"}
                assert tools["payments_refund"].input_schema["required"] == ["charge", "amount"]

                args = {"charge": "ch_0001", "amount": 2000}
                first = await client.call_tool("payments_refund", args)
                assert first.is_error is True
                assert _text(first)["error"] == "timeout"
                assert len(session.ledger.applied("payments", "refund")) == 1

                missing = await client.call_tool("payments_refund", {"charge": "ch_0001"})
                assert missing.is_error is True

                done = await client.call_tool("finish", {"status": "completed", "message": "ok"})
                assert done.is_error is False
                assert done.structured_content is not None
                assert done.structured_content["valid"] is True

    anyio.run(scenario)
    assert len(calls) == 2  # the rejected call never reached the session
    expected = run(scenarios["APM-S001"], reference_agent("naive"))
    got = session.result()
    assert [(v.oracle, v.passed) for v in got.verdicts] == [
        (v.oracle, v.passed) for v in expected.verdicts
    ]
    assert got.ledger.to_list() == expected.ledger.to_list()
    assert got.valid and expected.valid
    assert got.meta == {"transport": "mcp"}


def test_mcp_rate_limit_and_wait_match_the_careful_agent(scenarios: dict[str, Scenario]) -> None:
    scenario_def = scenarios["APM-S019"]
    session = Session(scenario_def, agent_name="mcp-agent", transport="mcp")
    server = build_server(session)

    async def scenario() -> None:
        with anyio.fail_after(TIMEOUT):
            async with Client(server) as client:
                for step in scenario_def.plan:
                    key = f"{step.id}-v1"
                    while True:
                        result = await client.call_tool(
                            "llm_complete", {**step.args, "idempotency_key": key}
                        )
                        if not result.is_error:
                            break
                        body = _text(result)
                        assert body["error"] == "rate_limit"
                        await client.call_tool("wait", {"ticks": body["retry_after"]})
                await client.call_tool("finish", {"status": "completed", "message": "Triaged."})

    anyio.run(scenario)
    expected = run(scenario_def, reference_agent("careful"))
    got = session.result()
    assert [(v.oracle, v.passed) for v in got.verdicts] == [
        (v.oracle, v.passed) for v in expected.verdicts
    ]
    assert got.passed and got.valid
    assert got.ledger.to_list() == expected.ledger.to_list()


def test_apm_serve_over_stdio(tmp_path: Path) -> None:
    out = tmp_path / "out"
    env = {k: v for k, v in os.environ.items() if k != "APM_SCENARIOS"}
    env[GENERATED_AT_ENV] = PINNED
    params = StdioServerParameters(
        command=sys.executable,
        args=[
            "-m",
            "agentpostmortem.cli",
            "serve",
            "--scenario",
            "APM-S004",
            "--agent-name",
            "stdio-agent",
            "--out",
            str(out),
            "--scenarios",
            str(SCENARIOS),
        ],
        env=env,
    )

    async def scenario() -> None:
        with anyio.fail_after(TIMEOUT):
            async with Client(params) as client:
                listing = await client.list_tools()
                names = {t.name for t in listing.tools}
                assert {"payments_charge", "email_send", "inventory_reserve"} <= names
                charged = await client.call_tool(
                    "payments_charge", {"customer": "cus_ada", "amount": 4999}
                )
                assert charged.is_error is False
                await client.call_tool("finish", {"status": "failed", "message": "Stopped."})

    anyio.run(scenario)
    results = load_results(out / "results.json")
    record = results["runs"][0]
    assert results["agents"] == ["stdio-agent"]
    assert record["meta"] == {"transport": "mcp"}
    assert record["valid"] is False
    assert "crash injection is not supported" in record["invalid_reasons"][0]
    assert record["ledger"][0]["operation"] == "charge"
    assert (out / "report.html").is_file()

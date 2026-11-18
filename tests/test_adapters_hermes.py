"""The Hermes Agent adapter: its scripted endpoint, and real Hermes runs when available.

Hermes runs from a source checkout in its own environment, located through
``APM_HERMES_SRC`` (and ``APM_HERMES_PYTHON``). Without it the end-to-end tests
skip; the endpoint tests below need no Hermes at all.
"""

from __future__ import annotations

import http.client
import json
import threading
from collections.abc import Iterator
from typing import Any

import pytest
from typing_extensions import TypedDict

from agentpostmortem import adapters
from agentpostmortem.adapters import FrameworkNotInstalledError, get_adapter, hermes
from agentpostmortem.adapters._script import ArgSpec, PlanScript, tool_specs
from agentpostmortem.domain.scenario import Scenario
from agentpostmortem.runner import build_world, run

# True means every oracle passes for Hermes with default settings.
EXPECTED = {
    "APM-S001": True,
    "APM-S002": True,
    "APM-S003": False,
    "APM-S004": False,
    "APM-S005": False,
    "APM-S008": True,
}


@pytest.fixture(scope="module")
def agent() -> hermes.HermesAgent:
    try:
        return hermes.make("hermes")
    except FrameworkNotInstalledError as error:
        pytest.skip(str(error))


# Without Hermes.


def test_registry_entry() -> None:
    info = adapters.ADAPTERS["hermes"]
    assert info.framework == "hermes"
    assert info.module == "agentpostmortem.adapters.hermes"


def test_missing_checkout_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(hermes.SRC_ENV, raising=False)
    with pytest.raises(FrameworkNotInstalledError, match=hermes.SRC_ENV):
        get_adapter("hermes")
    assert "hermes" not in adapters.available()


def test_checkout_without_hermes_is_reported(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    monkeypatch.setenv(hermes.SRC_ENV, str(tmp_path))
    with pytest.raises(FrameworkNotInstalledError, match=r"run_agent\.py"):
        hermes.locate()


def test_hermes_tool_errors_are_read_as_errors() -> None:
    error = '{"error": "[TOOL_ERROR] Tool execution failed: ToolTimeout: timed out"}'
    assert hermes.result_text(error).startswith("Error: ")
    assert hermes.result_text('{"id": "ch_1", "ok": true}') == '{"id":"ch_1","ok":true}'
    # A guardrail notice appended after a result does not hide the result.
    assert hermes.result_text('{"id": "ch_1"}\n\n[notice] stop repeating') == '{"id":"ch_1"}'


def test_observed_pairs_calls_with_results() -> None:
    messages: list[dict[str, Any]] = [
        {"role": "user", "content": "task"},
        {"role": "assistant", "tool_calls": [{"id": "call_apm_a", "function": {"name": "x"}}]},
        {"role": "tool", "tool_call_id": "call_apm_a", "content": '{"ok": true}'},
        {"role": "assistant", "tool_calls": [{"id": "call_apm_b", "function": {"name": "x"}}]},
    ]
    seen = hermes.observed(messages)
    assert [(o.call_id, o.result) for o in seen] == [
        ("call_apm_a", '{"ok":true}'),
        ("call_apm_b", None),
    ]


def test_tool_schemas_follow_the_plan(scenarios: dict[str, Scenario]) -> None:
    schemas = hermes.tool_schemas(tool_specs(scenarios["APM-S004"]))
    charge = next(s for s in schemas if s["name"] == "payments_charge")
    assert charge["parameters"]["properties"]["amount"] == {"type": "integer"}
    assert set(charge["parameters"]["required"]) == {"customer", "amount"}


class Endpoint:
    """Talks to the adapter's endpoint the way Hermes does."""

    def __init__(self, scenario: Scenario) -> None:
        _, self.tools = build_world(scenario)
        script = PlanScript(scenario, self.tools)
        specs = tool_specs(scenario)
        self.session = hermes._Session(script, self.tools, {s.name: s for s in specs})
        self.server = hermes._Server(self.session)
        self.offered = [{"type": "function", "function": s} for s in hermes.tool_schemas(specs)]
        self.messages: list[dict[str, Any]] = [{"role": "user", "content": scenario.task}]

    def post(self, path: str, payload: dict[str, Any]) -> tuple[int, bytes]:
        host, port = self.server.server_address[:2]
        connection = http.client.HTTPConnection(str(host), int(port), timeout=10)
        try:
            connection.request("POST", path, json.dumps(payload).encode())
            response = connection.getresponse()
            return response.status, response.read()
        finally:
            connection.close()

    def turn(self, *, stream: bool = False) -> dict[str, Any]:
        payload = {"messages": self.messages, "tools": self.offered, "stream": stream}
        status, body = self.post("/v1/chat/completions", payload)
        assert status == 200
        if not stream:
            message: dict[str, Any] = json.loads(body)["choices"][0]["message"]
            return message
        events = [
            json.loads(line[len("data: ") :])
            for line in body.decode().splitlines()
            if line.startswith("data: {")
        ]
        delta = events[0]["choices"][0]["delta"]
        return {"role": "assistant", "content": delta.get("content"), **delta}

    def call(self, message: dict[str, Any]) -> None:
        self.messages.append(message)
        call = message["tool_calls"][0]
        _, body = self.post(
            "/apm/tool",
            {
                "name": call["function"]["name"],
                "call_id": call["id"],
                "arguments": json.loads(call["function"]["arguments"]),
            },
        )
        reply = json.loads(body)
        content = reply.get("result") or json.dumps({"error": reply["message"]})
        self.messages.append({"role": "tool", "tool_call_id": call["id"], "content": content})


@pytest.fixture
def endpoint(request: pytest.FixtureRequest, scenarios: dict[str, Scenario]) -> Iterator[Endpoint]:
    served = Endpoint(scenarios[request.param])
    thread = threading.Thread(target=served.server.serve_forever, daemon=True)
    thread.start()
    yield served
    served.server.shutdown()
    served.server.server_close()


@pytest.mark.parametrize("endpoint", ["APM-S005"], indirect=True)
@pytest.mark.parametrize("stream", [False, True])
def test_endpoint_runs_the_plan(endpoint: Endpoint, stream: bool) -> None:
    for step in ("check", "reserve"):
        message = endpoint.turn(stream=stream)
        assert message["tool_calls"][0]["id"] == f"call_apm_{step}"
        endpoint.call(message)
    final = endpoint.turn(stream=stream)
    assert final["content"] == "Done. I have completed the task."
    assert [(c.step, c.result) for c in endpoint.tools.calls] == [
        ("check", "ok"),
        ("reserve", "conflict"),
    ]


@pytest.mark.parametrize("endpoint", ["APM-S004"], indirect=True)
def test_endpoint_withholds_the_answer_on_crash(endpoint: Endpoint) -> None:
    endpoint.call(endpoint.turn())
    with pytest.raises((http.client.HTTPException, ConnectionError)):
        endpoint.turn()
    assert endpoint.session.crash is not None
    status, _ = endpoint.post("/apm/tool", {"name": "email_send", "call_id": "call_apm_receipt"})
    assert status == 503
    assert [c.step for c in endpoint.tools.calls] == ["charge"]


@pytest.mark.parametrize("endpoint", ["APM-S001"], indirect=True)
def test_side_requests_do_not_advance_the_plan(endpoint: Endpoint) -> None:
    status, body = endpoint.post(
        "/v1/chat/completions", {"messages": [{"role": "user", "content": "Title?"}]}
    )
    assert status == 200
    assert json.loads(body)["choices"][0]["message"]["content"] == "OK"
    assert endpoint.session.other_requests == 1
    assert endpoint.tools.calls == []


# With Hermes.


@pytest.mark.parametrize("scenario_id", sorted(EXPECTED))
def test_expected_verdict(
    agent: hermes.HermesAgent, scenarios: dict[str, Scenario], scenario_id: str
) -> None:
    result = run(scenarios[scenario_id], agent)
    detail = "; ".join(f"{v.oracle}: {v.message}" for v in result.verdicts)
    assert result.passed is EXPECTED[scenario_id], detail
    assert result.meta["framework"] == "hermes"
    assert result.meta["framework_version"].startswith("hermes-agent ")


def test_tool_errors_reach_the_model_and_the_run_goes_on(
    agent: hermes.HermesAgent, scenarios: dict[str, Scenario]
) -> None:
    result = run(scenarios["APM-S005"], agent)
    assert [(c.step, c.result) for c in result.calls] == [("check", "ok"), ("reserve", "conflict")]
    assert result.outcome.status.value == "completed"
    assert result.outcome.completed_steps == ("check",)


def test_crash_restarts_the_run_from_the_top(
    agent: hermes.HermesAgent, scenarios: dict[str, Scenario]
) -> None:
    result = run(scenarios["APM-S004"], agent)
    assert result.restarts == 1
    assert [c.step for c in result.calls] == ["charge", "charge", "receipt", "reserve"]
    assert result.outcome.status.value == "completed"


@pytest.mark.parametrize("scenario_id", ["APM-S001", "APM-S004", "APM-S005"])
def test_runs_are_byte_identical(
    agent: hermes.HermesAgent, scenarios: dict[str, Scenario], scenario_id: str
) -> None:
    first = json.dumps(run(scenarios[scenario_id], agent).to_dict(), sort_keys=True)
    second = json.dumps(run(scenarios[scenario_id], agent).to_dict(), sort_keys=True)
    assert first == second


@pytest.mark.parametrize("scenario_id", ["APM-S004", "APM-S007"])
def test_without_faults_the_plan_runs_once_and_completes(
    agent: hermes.HermesAgent, scenarios: dict[str, Scenario], scenario_id: str
) -> None:
    scenario = scenarios[scenario_id]
    calm = scenario.model_copy(update={"faults": (), "crash": None})
    result = run(calm, agent)
    assert [c.step for c in result.calls] == [s.id for s in scenario.plan]
    assert result.outcome.status.value == "completed"
    assert result.outcome.completed_steps == tuple(s.id for s in scenario.plan)


class Line(TypedDict):
    sku: str
    quantity: int


def test_tool_schemas_for_script_generated_typeddicts() -> None:
    """The real shapes _script builds for dict arguments become object schemas."""
    specs = tool_specs(
        Scenario.model_validate(
            {
                "id": "APM-S901",
                "title": "t",
                "category": "bad_data",
                "incident": {"pattern": "p", "summary": "s"},
                "task": "t",
                "services": ["kv"],
                "plan": [
                    {
                        "id": "save",
                        "service": "kv",
                        "operation": "put",
                        "args": {"key": "k", "value": {"email": "a@b.co", "plan": "pro"}},
                    }
                ],
                "oracles": [{"name": "no_duplicate_effects"}],
            }
        )
    )
    value = next(arg for spec in specs for arg in spec.args if arg.name == "value")
    schema = hermes._arg_schema(value)
    assert schema["type"] == "object"
    assert set(schema["properties"]) == {"email", "plan"}


def test_tool_schemas_type_lists_and_objects() -> None:
    """Hermes gets real array and object schemas, not strings, for structured plan args."""
    assert hermes._arg_schema(ArgSpec("to", (list[str],), True)) == {
        "type": "array",
        "items": {"type": "string"},
    }
    assert hermes._arg_schema(ArgSpec("line", (Line,), True)) == {
        "type": "object",
        "properties": {"sku": {"type": "string"}, "quantity": {"type": "integer"}},
        "required": ["quantity", "sku"],
    }
    assert hermes._arg_schema(ArgSpec("note", (str, type(None)), False)) == {
        "type": ["string", "null"]
    }

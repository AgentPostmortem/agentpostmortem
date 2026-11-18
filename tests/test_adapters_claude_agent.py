"""The Claude Agent SDK adapter: its scripted endpoint, and real SDK runs when available.

The SDK (``claude-agent-sdk``) drives the Claude Code CLI, which its wheel
bundles. Without the SDK or a CLI the end-to-end tests skip; the endpoint and
conversation tests below need neither.
"""

from __future__ import annotations

import http.client
import importlib.util
import json
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from agentpostmortem import adapters
from agentpostmortem.adapters import FrameworkNotInstalledError, claude_agent, get_adapter
from agentpostmortem.adapters._script import ArgSpec, PlanScript, ToolSpec, _arg_types, tool_specs
from agentpostmortem.domain.scenario import Scenario
from agentpostmortem.runner import build_world, run

VARIANTS = ("claude-agent-sdk", "claude-agent-sdk-resume")

# Scenarios where every oracle passes, for both variants; every other scenario fails.
PASSING = {"APM-S001", "APM-S002", "APM-S008", "APM-S022"}

# Scenarios whose declared fault never fires with this framework (the run is invalid).
INVALID = {"APM-S006"}


@pytest.fixture(scope="module", params=VARIANTS)
def agent(request: pytest.FixtureRequest) -> claude_agent.ClaudeAgentSDKAgent:
    try:
        return claude_agent.make(request.param)
    except FrameworkNotInstalledError as error:
        pytest.skip(str(error))


@pytest.fixture(scope="module")
def default_agent() -> claude_agent.ClaudeAgentSDKAgent:
    try:
        return claude_agent.make("claude-agent-sdk")
    except FrameworkNotInstalledError as error:
        pytest.skip(str(error))


# Without the SDK.


def test_registry_entries() -> None:
    for name in VARIANTS:
        info = adapters.ADAPTERS[name]
        assert info.framework == "claude-agent-sdk"
        assert info.module == "agentpostmortem.adapters.claude_agent"
        assert info.requires == ("claude_agent_sdk",)
        assert info.locator == "is_available"


def test_missing_sdk_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    real = importlib.util.find_spec

    def find_spec(name: str, *args: Any) -> Any:
        return None if name.partition(".")[0] == "claude_agent_sdk" else real(name, *args)

    monkeypatch.setattr(importlib.util, "find_spec", find_spec)
    monkeypatch.setattr(claude_agent, "find_spec", find_spec)
    for name in VARIANTS:
        with pytest.raises(FrameworkNotInstalledError, match="frameworks"):
            get_adapter(name)
        with pytest.raises(FrameworkNotInstalledError, match="claude-agent-sdk"):
            claude_agent.make(name)
        assert name not in adapters.available()


def test_missing_cli_is_reported(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv(claude_agent.CLI_ENV, str(tmp_path / "nothing"))
    with pytest.raises(FrameworkNotInstalledError, match=claude_agent.CLI_ENV):
        claude_agent.locate_cli()
    monkeypatch.delenv(claude_agent.CLI_ENV)
    monkeypatch.setattr(claude_agent, "bundled_cli", lambda: None)
    monkeypatch.setenv("PATH", str(tmp_path))
    with pytest.raises(FrameworkNotInstalledError, match="Claude Code CLI"):
        claude_agent.locate_cli()
    assert not claude_agent.is_available()


def test_unknown_variant() -> None:
    with pytest.raises(KeyError):
        claude_agent.make("claude-agent-sdk-nope")


def test_tool_errors_are_read_as_errors() -> None:
    error = {"type": "tool_result", "tool_use_id": "x", "content": "timed out", "is_error": True}
    assert claude_agent.result_text(error) == "Error: timed out"
    ok = {"type": "tool_result", "content": [{"type": "text", "text": '{"ok": true, "id": "c"}'}]}
    assert claude_agent.result_text(ok) == '{"id":"c","ok":true}'


def test_observed_pairs_calls_with_results() -> None:
    messages: list[dict[str, Any]] = [
        {"role": "user", "content": "task"},
        {"role": "system", "content": "environment"},
        {"role": "assistant", "content": [{"type": "tool_use", "id": "call_apm_a", "input": {}}]},
        {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "call_apm_a", "content": "{}"}],
        },
        {"role": "assistant", "content": [{"type": "tool_use", "id": "call_apm_b", "input": {}}]},
    ]
    seen = claude_agent.observed(messages)
    assert [(o.call_id, o.result) for o in seen] == [("call_apm_a", "{}"), ("call_apm_b", None)]
    assert not claude_agent.ends_with_result(messages)
    assert claude_agent.ends_with_result([*messages[:4], {"role": "system", "content": "note"}])


def test_input_schemas_follow_the_plan(scenarios: dict[str, Scenario]) -> None:
    specs = {s.name: s for s in tool_specs(scenarios["APM-S004"])}
    schema = claude_agent.input_schema(specs["payments_charge"])
    assert schema["properties"]["amount"] == {"type": "integer"}
    assert set(schema["required"]) == {"customer", "amount"}


def test_isolated_environment_blanks_everything_inherited(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "secret")
    monkeypatch.setenv("CLAUDE_CODE_USE_BEDROCK", "1")
    env = claude_agent.run_env(tmp_path, "http://127.0.0.1:1")
    assert env["ANTHROPIC_AUTH_TOKEN"] == ""
    assert env["CLAUDE_CODE_USE_BEDROCK"] == ""
    assert env["ANTHROPIC_BASE_URL"] == env["HTTPS_PROXY"] == "http://127.0.0.1:1"
    assert env["ANTHROPIC_API_KEY"] == claude_agent.API_KEY
    assert env["CLAUDE_CONFIG_DIR"].startswith(str(tmp_path))
    assert env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] == "1"


def test_transcript_progress_is_read(tmp_path: Path) -> None:
    session = tmp_path / "project-key" / "session.jsonl"
    session.parent.mkdir()
    entries: list[dict[str, Any]] = [
        {"type": "user", "message": {"content": "task"}},
        {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "call_apm_a"}]}},
        {
            "type": "user",
            "message": {"content": [{"type": "tool_result", "tool_use_id": "call_apm_a"}]},
        },
        {"type": "assistant", "isSidechain": True, "message": {"content": [{"type": "tool_use"}]}},
    ]
    session.write_text("\n".join(json.dumps(e) for e in entries) + '\n{"partial', "utf-8")
    assert claude_agent.persisted(tmp_path) == ({"call_apm_a"}, {"call_apm_a"}, 1)
    messages: list[dict[str, Any]] = [
        {"role": "user", "content": "task"},
        {"role": "assistant", "content": [{"type": "tool_use", "id": "call_apm_a"}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "call_apm_a"}]},
    ]
    assert claude_agent.wait_until_persisted(tmp_path, messages, threading.Event())


class Endpoint:
    """Talks to the adapter's endpoint the way Claude Code does, and runs its tools."""

    def __init__(self, scenario: Scenario) -> None:
        _, self.tools = build_world(scenario)
        script = PlanScript(scenario, self.tools)
        self.specs = {s.name: s for s in tool_specs(scenario)}
        self.session = claude_agent._Session(script, self.tools, self.specs)
        self.server = claude_agent._Server(self.session)
        self.offered = [{"name": claude_agent.TOOL_PREFIX + n} for n in self.specs]
        self.messages: list[dict[str, Any]] = [{"role": "user", "content": scenario.task}]

    def post(self, path: str, payload: dict[str, Any], method: str = "POST") -> tuple[int, bytes]:
        host, port = self.server.server_address[:2]
        connection = http.client.HTTPConnection(str(host), int(port), timeout=10)
        try:
            connection.request(method, path, json.dumps(payload).encode())
            response = connection.getresponse()
            return response.status, response.read()
        finally:
            connection.close()

    def turn(self, *, stream: bool = False) -> dict[str, Any]:
        payload = {"model": "m", "messages": self.messages, "tools": self.offered, "stream": stream}
        status, body = self.post("/v1/messages?beta=true", payload)
        assert status == 200
        if not stream:
            message: dict[str, Any] = json.loads(body)
            return message
        events = [
            json.loads(line[len("data: ") :])
            for line in body.decode().splitlines()
            if line.startswith("data: ")
        ]
        assert events[0]["type"] == "message_start"
        assert events[-1]["type"] == "message_stop"
        blocks: list[dict[str, Any]] = []
        for event in events:
            if event["type"] == "content_block_start":
                blocks.append(dict(event["content_block"]))
            elif event["type"] == "content_block_delta" and "partial_json" in event["delta"]:
                blocks[-1]["input"] = json.loads(event["delta"]["partial_json"])
            elif event["type"] == "content_block_delta":
                blocks[-1]["text"] += event["delta"]["text"]
        return {"role": "assistant", "content": blocks}

    def call(self, message: dict[str, Any]) -> None:
        self.messages.append({"role": "assistant", "content": message["content"]})
        use = message["content"][0]
        spec = self.specs[use["name"].removeprefix(claude_agent.TOOL_PREFIX)]
        try:
            text = claude_agent.run_plan_tool(self.session, spec, use["input"])
            result = {"type": "tool_result", "tool_use_id": use["id"], "content": text}
        except Exception as error:
            result = {
                "type": "tool_result",
                "tool_use_id": use["id"],
                "content": str(error),
                "is_error": True,
            }
        self.messages.append({"role": "user", "content": [result]})


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
        assert message["content"][0]["id"] == f"call_apm_{step}"
        assert message["content"][0]["name"].startswith(claude_agent.TOOL_PREFIX)
        endpoint.call(message)
    final = endpoint.turn(stream=stream)
    assert final["content"] == [{"type": "text", "text": "Done. I have completed the task."}]
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
    status, _ = endpoint.post("/v1/messages", {"messages": endpoint.messages})
    assert status == 503
    with pytest.raises(RuntimeError, match="ended"):
        claude_agent.run_plan_tool(endpoint.session, endpoint.specs["email_send"], {})
    assert [c.step for c in endpoint.tools.calls] == ["charge"]


@pytest.mark.parametrize("endpoint", ["APM-S001"], indirect=True)
def test_side_requests_do_not_advance_the_plan(endpoint: Endpoint) -> None:
    status, body = endpoint.post(
        "/v1/messages", {"messages": [{"role": "user", "content": "Title?"}]}
    )
    assert status == 200
    assert json.loads(body)["content"] == [{"type": "text", "text": "OK"}]
    status, body = endpoint.post("/v1/messages/count_tokens", {"messages": []})
    assert (status, json.loads(body)) == (200, {"input_tokens": 0})
    assert endpoint.session.other_requests == 1
    assert endpoint.tools.calls == []


@pytest.mark.parametrize("endpoint", ["APM-S001"], indirect=True)
def test_tools_run_only_for_model_calls(endpoint: Endpoint) -> None:
    with pytest.raises(RuntimeError, match="without a model call"):
        claude_agent.run_plan_tool(endpoint.session, endpoint.specs["payments_refund"], {})
    assert endpoint.session.failure is not None
    assert endpoint.tools.calls == []


@pytest.mark.parametrize("endpoint", ["APM-S001"], indirect=True)
def test_network_requests_are_refused_and_recorded(endpoint: Endpoint) -> None:
    status, _ = endpoint.post("api.anthropic.com:443", {}, method="CONNECT")
    assert status == 403
    status, _ = endpoint.post("http://example.com/", {}, method="GET")
    assert status == 403
    assert endpoint.session.blocked == ["CONNECT api.anthropic.com:443", "GET http://example.com/"]


# With the SDK and its CLI.


@pytest.mark.parametrize("scenario_id", sorted(f"APM-S{n:03d}" for n in range(1, 26)))
def test_expected_verdict(
    agent: claude_agent.ClaudeAgentSDKAgent, scenarios: dict[str, Scenario], scenario_id: str
) -> None:
    result = run(scenarios[scenario_id], agent)
    detail = "; ".join(f"{v.oracle}: {v.message}" for v in result.verdicts)
    assert result.passed is (scenario_id in PASSING), detail
    assert result.valid is (scenario_id not in INVALID), result.invalid_reasons
    assert result.meta["framework"] == "claude-agent-sdk"
    assert result.meta["framework_version"].startswith("claude-agent-sdk ")
    assert "(Claude Code " in result.meta["framework_version"]


def test_tool_errors_reach_the_model_and_the_run_goes_on(
    default_agent: claude_agent.ClaudeAgentSDKAgent, scenarios: dict[str, Scenario]
) -> None:
    result = run(scenarios["APM-S005"], default_agent)
    assert [(c.step, c.result) for c in result.calls] == [("check", "ok"), ("reserve", "conflict")]
    assert result.outcome.status.value == "completed"
    assert result.outcome.completed_steps == ("check",)


def test_crash_restarts_the_session_from_the_top(
    default_agent: claude_agent.ClaudeAgentSDKAgent, scenarios: dict[str, Scenario]
) -> None:
    result = run(scenarios["APM-S014"], default_agent)
    assert result.restarts == 2
    assert [c.step for c in result.calls] == [
        "ticket_ada",
        "ticket_bo",
        "ticket_ada",
        "ticket_bo",
        "ticket_ada",
        "ticket_bo",
        "ticket_cy",
    ]


def test_resume_keeps_earlier_steps_but_repeats_the_one_in_flight(
    scenarios: dict[str, Scenario],
) -> None:
    try:
        agent = claude_agent.make("claude-agent-sdk-resume")
    except FrameworkNotInstalledError as error:
        pytest.skip(str(error))
    result = run(scenarios["APM-S014"], agent)
    assert result.restarts == 2
    # The transcript on disk has ticket_ada, but not the step that ran just before
    # each kill: Claude Code writes it after sending the next model request.
    assert [c.step for c in result.calls] == [
        "ticket_ada",
        "ticket_bo",
        "ticket_bo",
        "ticket_bo",
        "ticket_cy",
    ]
    assert result.outcome.status.value == "completed"


@pytest.mark.parametrize("scenario_id", ["APM-S001", "APM-S004", "APM-S014"])
def test_runs_are_byte_identical(
    agent: claude_agent.ClaudeAgentSDKAgent, scenarios: dict[str, Scenario], scenario_id: str
) -> None:
    first = json.dumps(run(scenarios[scenario_id], agent).to_dict(), sort_keys=True)
    second = json.dumps(run(scenarios[scenario_id], agent).to_dict(), sort_keys=True)
    assert first == second


def test_without_faults_the_plan_runs_once_and_completes(
    default_agent: claude_agent.ClaudeAgentSDKAgent, scenarios: dict[str, Scenario]
) -> None:
    for scenario in scenarios.values():
        calm = scenario.model_copy(update={"faults": (), "crash": None})
        result = run(calm, default_agent)
        assert [c.step for c in result.calls] == [s.id for s in scenario.plan], scenario.id
        assert result.outcome.status.value == "completed"
        assert result.outcome.completed_steps == tuple(s.id for s in scenario.plan)


def test_structured_arguments_get_array_and_object_schemas() -> None:

    value = {"recipients": ["a@x", "b@x"], "limits": [1, "two"], "meta": {"n": 1}}
    spec = ToolSpec("svc", "op", tuple(ArgSpec(k, _arg_types(v), True) for k, v in value.items()))
    props = claude_agent.input_schema(spec)["properties"]
    assert props["recipients"] == {"type": "array", "items": {"type": "string"}}
    assert props["limits"] == {"type": "array", "items": {"type": ["integer", "string"]}}
    assert props["meta"] == {
        "type": "object",
        "properties": {"n": {"type": "integer"}},
        "required": ["n"],
    }

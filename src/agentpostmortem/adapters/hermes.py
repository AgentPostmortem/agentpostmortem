"""Hermes Agent in framework mode.

Hermes Agent (Nous Research) runs from a source checkout with its own Python
environment: the project publishes no supported wheel, and its dependencies
conflict with the other framework extras (it pins ``openai`` 2.x, the OpenAI
Agents SDK needs 3.x). The adapter therefore runs Hermes in a child process,
the way it is meant to be embedded (``from run_agent import AIAgent``), and
serves it two things on 127.0.0.1 from this process:

* An OpenAI-compatible ``/v1/chat/completions`` endpoint, configured as Hermes'
  documented custom provider (``model.provider: custom`` with ``base_url``).
  It is driven by the scenario plan: it reads the conversation Hermes sends and
  answers, streamed or not, with exactly one tool call per plan step, then a
  final message. This is the only model Hermes talks to.
* A tool endpoint for the ``agentpostmortem`` Hermes plugin
  (``integrations/hermes/plugin``), which registers one Hermes tool per service
  operation through ``ctx.register_tool`` and forwards each call here, where it
  runs against the simulated service. A failed call makes the Hermes tool raise.

Hermes' own agent loop, tool dispatch and tool error handling are untouched:
whatever Hermes does with a raised tool exception is what the run shows.

Each run gets a throwaway ``HERMES_HOME`` and ``HOME``, so no user
configuration, memory, rules or credentials are read. The settings written
there only make the run possible and keep it local: the custom endpoint, the
plugin enabled, and Tool Search off so the plan's tools are offered to the model
directly instead of behind Hermes' search bridge. The agent is built with
``quiet_mode``, ``skip_context_files``, ``skip_memory`` and
``skip_background_review`` (all documented ``AIAgent`` arguments), none of which
changes tool or error handling. The child process refuses every non-loopback
connection.

A simulated crash kills the Hermes process at the moment it asks the model for
its next turn after a step, which is when Hermes has appended the tool result to
its conversation. That is the same boundary the other adapters use.

Locating Hermes:

* ``APM_HERMES_SRC``: a Hermes Agent source checkout (required).
* ``APM_HERMES_PYTHON``: the Python of its environment. Defaults to the
  checkout's ``.venv``, where Hermes' own setup puts it.

Variants: ``hermes`` only. Hermes documents no setting that retries failed
tools or changes how a tool exception reaches the model; its tool-loop
guardrails only warn about or stop repeated identical failing calls, which the
scripted plan never makes.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import typing
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from agentpostmortem.agents.base import CheckpointStore
from agentpostmortem.domain.outcome import AgentOutcome, Status
from agentpostmortem.domain.scenario import Scenario
from agentpostmortem.sims.errors import AgentCrashed
from agentpostmortem.toolbox import Toolbox

from . import FrameworkNotInstalledError
from ._script import (
    Action,
    ArgSpec,
    FinalAnswer,
    Observed,
    PlanScript,
    ToolSpec,
    call_tool,
    encode_result,
    tool_specs,
)

FRAMEWORK = "hermes"
SRC_ENV = "APM_HERMES_SRC"
PYTHON_ENV = "APM_HERMES_PYTHON"
PLUGIN_ENV = "APM_HERMES_PLUGIN"
MODEL = "agentpostmortem-scripted"
RUN_TIMEOUT_SECONDS = 300.0
INSTALL_HINT = (
    f"set {SRC_ENV} to a Hermes Agent source checkout prepared with its own environment "
    f"(see integrations/hermes/plugin/README.md), and {PYTHON_ENV} if its Python is not in .venv"
)

_JSON_TYPES = {
    bool: "boolean",
    int: "integer",
    float: "number",
    str: "string",
    type(None): "null",
}


def _json_schema(kind: Any) -> dict[str, Any]:
    """JSON Schema for one annotation from the plan: scalars, ``list[...]`` or a TypedDict."""
    if typing.get_origin(kind) is list:
        items = typing.get_args(kind)
        if not items:
            return {"type": "array"}
        schemas = [_json_schema(item) for item in items]
        return {"type": "array", "items": schemas[0] if len(schemas) == 1 else {"anyOf": schemas}}
    if typing.is_typeddict(kind):
        hints = typing.get_type_hints(kind)
        return {
            "type": "object",
            "properties": {name: _json_schema(hint) for name, hint in hints.items()},
            "required": sorted(getattr(kind, "__required_keys__", hints)),
        }
    return {"type": _JSON_TYPES.get(kind, "string")}


@dataclass(frozen=True)
class HermesInstall:
    src: Path
    python: Path
    plugin: Path


def _default_plugin_dir() -> Path:
    # src/agentpostmortem/adapters/hermes.py -> repository root.
    return Path(__file__).resolve().parents[3] / "integrations" / "hermes" / "plugin"


def locate() -> HermesInstall:
    """Find the Hermes checkout, its Python and the plugin, or explain what is missing."""
    src_value = os.environ.get(SRC_ENV, "")
    if not src_value:
        raise FrameworkNotInstalledError(f"adapter 'hermes' needs Hermes Agent: {INSTALL_HINT}")
    src = Path(src_value).expanduser()
    if not (src / "run_agent.py").is_file():
        raise FrameworkNotInstalledError(f"{SRC_ENV}={src} has no run_agent.py; {INSTALL_HINT}")
    python = Path(os.environ.get(PYTHON_ENV) or src / ".venv" / "bin" / "python").expanduser()
    if not python.is_file():
        raise FrameworkNotInstalledError(f"no Hermes Python at {python}; {INSTALL_HINT}")
    plugin = Path(os.environ.get(PLUGIN_ENV) or _default_plugin_dir()).expanduser()
    if not (plugin / "run_hermes.py").is_file():
        raise FrameworkNotInstalledError(
            f"the agentpostmortem Hermes plugin is not at {plugin}; set {PLUGIN_ENV} to the "
            "integrations/hermes/plugin directory of an AgentPostmortem checkout"
        )
    return HermesInstall(src, python, plugin)


def is_available() -> bool:
    try:
        locate()
    except FrameworkNotInstalledError:
        return False
    return True


def framework_version(src: Path) -> str:
    """Hermes' release date and the checkout's commit (a source checkout has no version)."""
    release = "unknown"
    init = src / "hermes_cli" / "__init__.py"
    if init.is_file():
        found = re.search(r'__release_date__\s*=\s*"([^"]+)"', init.read_text(encoding="utf-8"))
        if found:
            release = found.group(1)
    commit = _git_head(src)
    return f"hermes-agent {release} ({commit})"


def _git_head(src: Path) -> str:
    git = shutil.which("git")
    if git is None:
        return "unknown commit"
    try:
        out = subprocess.run(
            [git, "-C", str(src), "rev-parse", "--short=8", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown commit"
    return out.stdout.strip() or "unknown commit"


# The conversation, as Hermes sends it in OpenAI chat-completions format.


def result_text(content: Any) -> str:
    """A tool message as the script reads it.

    Hermes reports a tool that raised as ``{"error": "..."}`` (``tools.registry.
    tool_error``), so that is an error, not a result. Text Hermes appends after a
    result, such as a tool-loop guardrail notice, does not hide the result.
    """
    text = content if isinstance(content, str) else json.dumps(content)
    try:
        value, _ = json.JSONDecoder().raw_decode(text.strip())
    except ValueError:
        return text
    if not isinstance(value, dict):
        return text
    if "error" in value:
        return f"Error: {value['error']}"
    return encode_result(value)


def observed(messages: Sequence[Mapping[str, Any]]) -> list[Observed]:
    """Every tool call in a chat-completions conversation, with its result if present."""
    results = {
        str(m.get("tool_call_id")): result_text(m.get("content"))
        for m in messages
        if m.get("role") == "tool"
    }
    calls: list[Observed] = []
    for message in messages:
        if message.get("role") != "assistant":
            continue
        for call in message.get("tool_calls") or ():
            identifier = str(call.get("id") or call.get("call_id") or "")
            calls.append(Observed(identifier, results.get(identifier)))
    return calls


def tool_schemas(specs: Iterable[ToolSpec]) -> list[dict[str, Any]]:
    """OpenAI function schemas for the plan's tools, as the plugin registers them."""
    return [
        {
            "name": spec.name,
            "description": spec.description,
            "parameters": {
                "type": "object",
                "properties": {arg.name: _arg_schema(arg) for arg in spec.args},
                "required": [arg.name for arg in spec.args if arg.required],
            },
        }
        for spec in specs
    ]


def _arg_schema(arg: ArgSpec) -> dict[str, Any]:
    schemas = [_json_schema(kind) for kind in arg.types]
    if len(schemas) == 1:
        return schemas[0]
    if all(set(schema) == {"type"} for schema in schemas):
        return {"type": [schema["type"] for schema in schemas]}
    return {"anyOf": schemas}


def _message(action: Action) -> tuple[dict[str, Any], str]:
    if isinstance(action, FinalAnswer):
        return {"role": "assistant", "content": action.text}, "stop"
    call = {
        "id": action.call_id,
        "type": "function",
        "function": {
            "name": action.name,
            "arguments": json.dumps(action.arguments, sort_keys=True),
        },
    }
    return {"role": "assistant", "content": None, "tool_calls": [call]}, "tool_calls"


# The local endpoint Hermes talks to.


@dataclass
class _Session:
    """State shared by the endpoint threads and the run."""

    script: PlanScript
    tools: Toolbox
    specs: dict[str, ToolSpec]
    lock: threading.Lock = field(default_factory=threading.Lock)
    stopped: threading.Event = field(default_factory=threading.Event)
    crash: AgentCrashed | None = None
    failure: str | None = None
    turns: int = 0
    other_requests: int = 0


class _Handler(BaseHTTPRequestHandler):
    server: _Server

    def log_message(self, format: str, *args: Any) -> None:
        return

    def do_GET(self) -> None:
        if self.path.rstrip("/") in ("/v1/models", f"/v1/models/{MODEL}"):
            model = {"id": MODEL, "object": "model", "owned_by": "agentpostmortem"}
            self._json(
                200, model if self.path.endswith(MODEL) else {"object": "list", "data": [model]}
            )
        else:
            self._json(404, {"error": {"message": "not found"}})

    def do_POST(self) -> None:
        session = self.server.session
        length = int(self.headers.get("Content-Length") or 0)
        try:
            request = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            self._json(400, {"error": {"message": "invalid JSON"}})
            return
        if session.stopped.is_set():
            self._json(503, {"error": {"message": "the run has ended"}})
        elif self.path == "/apm/tool":
            self._tool(session, request)
        elif self.path.rstrip("/") == "/v1/chat/completions":
            self._chat(session, request)
        else:
            self._json(404, {"error": {"message": "not found"}})

    def _tool(self, session: _Session, request: Mapping[str, Any]) -> None:
        with session.lock:
            try:
                spec = session.specs[str(request.get("name"))]
                step = session.script.step_for(str(request.get("call_id")))
            except KeyError as error:
                session.failure = f"Hermes ran a tool call the plan does not contain: {error}"
                session.stopped.set()
                self._json(500, {"error_type": "AdapterError", "message": session.failure})
                return
            arguments = request.get("arguments") or {}
            try:
                result = call_tool(session.tools, step, spec.service, spec.operation, arguments)
            except Exception as error:
                self._json(200, {"error_type": type(error).__name__, "message": str(error)})
                return
        self._json(200, {"result": result})

    def _chat(self, session: _Session, request: Mapping[str, Any]) -> None:
        offered = {
            str((tool.get("function") or {}).get("name")) for tool in request.get("tools") or ()
        }
        messages: list[Mapping[str, Any]] = list(request.get("messages") or ())
        if not offered & set(session.specs):
            # A side request (for example a title or summary), not the agent's turn.
            with session.lock:
                session.other_requests += 1
            self._reply(request, {"role": "assistant", "content": "OK"}, "stop")
            return
        last_is_result = bool(messages) and messages[-1].get("role") == "tool"
        with session.lock:
            session.turns += 1
            try:
                action = session.script.next_action(
                    observed(messages), last_is_result=last_is_result
                )
            except AgentCrashed as crash:
                # No answer: the run kills Hermes now, and every later request is refused.
                session.crash = crash
                session.stopped.set()
                return
            except Exception as error:
                session.failure = f"The scripted model failed: {type(error).__name__}: {error}"
                session.stopped.set()
                self._json(500, {"error": {"message": session.failure}})
                return
        message, finish = _message(action)
        self._reply(request, message, finish)

    def _reply(self, request: Mapping[str, Any], message: dict[str, Any], finish: str) -> None:
        session = self.server.session
        ident = f"chatcmpl-apm-{session.turns}"
        usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        base = {"id": ident, "created": 0, "model": MODEL}
        if not request.get("stream"):
            choice = {"index": 0, "message": message, "finish_reason": finish}
            self._json(
                200, {**base, "object": "chat.completion", "choices": [choice], "usage": usage}
            )
            return
        delta: dict[str, Any] = {"role": "assistant"}
        if message.get("content"):
            delta["content"] = message["content"]
        if message.get("tool_calls"):
            delta["tool_calls"] = [
                {"index": i, **call} for i, call in enumerate(message["tool_calls"])
            ]
        chunk = {**base, "object": "chat.completion.chunk"}
        events = [
            {**chunk, "choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
            {**chunk, "choices": [{"index": 0, "delta": {}, "finish_reason": finish}]},
            {**chunk, "choices": [], "usage": usage},
        ]
        body = b"".join(b"data: " + json.dumps(e).encode() + b"\n\n" for e in events)
        body += b"data: [DONE]\n\n"
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, payload: Mapping[str, Any]) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class _Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, session: _Session) -> None:
        super().__init__(("127.0.0.1", 0), _Handler)
        self.session = session


# The run.


def _config(endpoint: str) -> str:
    """The run's Hermes ``config.yaml``: the documented custom-endpoint setup."""
    return "\n".join(
        [
            "model:",
            f"  default: {MODEL}",
            "  provider: custom",
            f"  base_url: {endpoint}",
            "  api_key: agentpostmortem-local",
            "plugins:",
            "  enabled:",
            "    - agentpostmortem",
            "tools:",
            "  tool_search:",
            '    enabled: "off"',
            "",
        ]
    )


class HermesAgent:
    """Runs a scenario through a Hermes ``AIAgent`` with a scripted model endpoint."""

    framework = FRAMEWORK

    def __init__(self, name: str, install: HermesInstall) -> None:
        self.name = name
        self.install = install
        self.framework_version = framework_version(install.src)

    def run(self, scenario: Scenario, tools: Toolbox, store: CheckpointStore) -> AgentOutcome:
        script = PlanScript(scenario, tools)
        specs = tool_specs(scenario)
        session = _Session(script, tools, {spec.name: spec for spec in specs})
        server = _Server(session)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory(prefix="apm-hermes-") as workdir:
                return self._run_hermes(scenario, session, server, Path(workdir), specs)
        finally:
            session.stopped.set()
            server.shutdown()
            server.server_close()
            thread.join()

    def _run_hermes(
        self,
        scenario: Scenario,
        session: _Session,
        server: _Server,
        workdir: Path,
        specs: Sequence[ToolSpec],
    ) -> AgentOutcome:
        port = server.server_address[1]
        endpoint = f"http://127.0.0.1:{port}/v1"
        home = workdir / "hermes-home"
        shutil.copytree(
            self.install.plugin / "agentpostmortem", home / "plugins" / "agentpostmortem"
        )
        (home / "config.yaml").write_text(_config(endpoint), encoding="utf-8")
        tools_file = workdir / "tools.json"
        tools_file.write_text(json.dumps(tool_schemas(specs)), encoding="utf-8")
        result_file = workdir / "result.json"
        log_file = workdir / "hermes.log"
        env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": str(workdir),
            "HERMES_HOME": str(home),
            "LANG": "C.UTF-8",
            "TZ": "UTC",
            "APM_HERMES_TOOLS": str(tools_file),
            "APM_HERMES_ENDPOINT": f"127.0.0.1:{port}",
        }
        command = [
            str(self.install.python),
            "-I",
            str(self.install.plugin / "run_hermes.py"),
            str(self.install.src),
            endpoint,
            scenario.task,
            str(result_file),
        ]
        with log_file.open("wb") as log:
            process = subprocess.Popen(
                command, cwd=workdir, env=env, stdout=log, stderr=subprocess.STDOUT
            )
            try:
                self._wait(process, session)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait()
        if session.crash is not None:
            raise session.crash
        if session.failure is not None:
            raise RuntimeError(f"{session.failure}\n{_tail(log_file)}")
        if not result_file.is_file():
            return AgentOutcome(
                Status.FAILED,
                f"Hermes exited with code {process.returncode} without a result: {_tail(log_file)}",
            )
        result = json.loads(result_file.read_text(encoding="utf-8"))
        if result.get("blocked_network"):
            raise RuntimeError(f"Hermes tried to reach the network: {result['blocked_network']}")
        messages = [m for m in result.get("messages") or () if isinstance(m, dict)]
        completed = session.script.completed_steps(observed(messages))
        claim = str(result.get("final_response") or "")
        if result.get("failed"):
            reason = result.get("error") or claim
            return AgentOutcome(Status.FAILED, f"The Hermes run failed: {reason}", completed)
        return AgentOutcome(Status.COMPLETED, claim, completed)

    @staticmethod
    def _wait(process: subprocess.Popen[bytes], session: _Session) -> None:
        deadline = time.monotonic() + RUN_TIMEOUT_SECONDS
        while process.poll() is None:
            if session.stopped.wait(0.02):
                # A crash or an adapter failure: kill Hermes where it stands.
                return
            if time.monotonic() > deadline:
                session.failure = f"Hermes did not finish within {RUN_TIMEOUT_SECONDS:.0f}s"
                return


def _tail(path: Path, lines: int = 20) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return "\n".join(text.splitlines()[-lines:])


VARIANTS = ("hermes",)


def make(name: str) -> HermesAgent:
    if name not in VARIANTS:
        raise KeyError(name)
    return HermesAgent(name, locate())

"""The Claude Agent SDK in framework mode.

The Claude Agent SDK (``claude-agent-sdk``) is Claude Code packaged as a
library: ``query()`` starts the Claude Code CLI as a child process and drives
it over a JSON stream. The agent loop, tool dispatch, tool error handling and
session persistence all live in that CLI. The adapter keeps them as they are
and replaces only two things around them:

* The model. The CLI talks to the Anthropic Messages API at
  ``ANTHROPIC_BASE_URL`` with the key in ``ANTHROPIC_API_KEY`` (both documented
  Claude Code environment variables). The adapter serves a Messages API
  endpoint on 127.0.0.1 driven by the scenario plan: it reads the conversation
  the CLI sends and answers, as a server-sent event stream or as JSON, with
  exactly one ``tool_use`` per plan step, then a final message. The key is a
  dummy; no Anthropic account or credential is ever used.
* The tools. Each service operation of the plan is a custom tool made with the
  SDK's ``tool()`` decorator and served in process by ``create_sdk_mcp_server``,
  the SDK's documented custom tools API. Claude Code sees them as
  ``mcp__apm__<service>_<operation>``. A tool body is one toolbox call; when
  the call fails it raises, and the SDK turns the exception into an ``isError``
  tool result for the model, which is its own documented behaviour.

The options only make the run possible and keep it isolated: ``tools=[]`` (no
built-in Bash, file or web tools), ``allowed_tools`` naming the plan's tools so
they run without a permission prompt, ``strict_mcp_config`` and
``setting_sources=[]`` so no other MCP server or settings file is loaded, and a
throwaway ``cwd``, ``HOME`` and ``CLAUDE_CONFIG_DIR``. The SDK starts the CLI
with the parent's environment merged under ``options.env``, so every inherited
variable is overridden with an empty value and only the run's own variables are
set. Nonessential traffic, telemetry, error reporting and auto-updates are off
(``CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC``, ``DISABLE_TELEMETRY``,
``DISABLE_ERROR_REPORTING``, ``DISABLE_AUTOUPDATER``), and ``HTTP(S)_PROXY``
points at the local endpoint, which refuses and records any request for another
host, so an attempt to reach the network fails the run instead of leaving the
machine.

A simulated crash kills the CLI process (SIGKILL) at the moment it asks the
model for its next turn after a step, which is when Claude Code has appended
the tool result to its conversation. That is the same boundary the other
adapters use. The CLI is started through a small wrapper that records its
process id so it can be killed where it stands; the wrapper ``exec``s the CLI
and changes nothing else.

Pacing: Claude Code appends to its session transcript in the background, about
50 ms after it sends the request that contains the new entries (measured with
the bundled CLI 2.1.327). A real model answers far later than that, so the
scripted model holds each answer until the transcript holds the conversation it
was asked about (``wait_until_persisted``). Without this, a run that finishes in
milliseconds would lose the whole transcript at a crash, which no real run
would. The request on which a crash fires is never held: the CLI dies with that
request's new entries (the step's tool call and result) still unwritten, as it
would with a real model.

Variants change only settings the SDK documents:

* ``claude-agent-sdk``: defaults. Each run (and each restart after a crash)
  starts a new session.
* ``claude-agent-sdk-resume``: the session's configuration directory is kept
  across restarts and a restart passes ``resume=<session id>``, the SDK's
  documented way to continue a session from its transcript.

Locating the CLI: the SDK wheel bundles a Claude Code binary and prefers it
(``SubprocessCLITransport._find_cli``); without one it uses ``claude`` on
``PATH``. The adapter resolves the CLI the same way, and ``APM_CLAUDE_CLI``
overrides it.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import os
import shlex
import shutil
import signal
import subprocess
import tempfile
import threading
import time
import types
import typing
import uuid
from collections import deque
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from functools import cache
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.metadata import PackageNotFoundError, version
from importlib.util import find_spec
from pathlib import Path
from typing import Any

import typing_extensions

from agentpostmortem.agents.base import CheckpointStore
from agentpostmortem.domain.outcome import AgentOutcome, Status
from agentpostmortem.domain.scenario import Scenario
from agentpostmortem.sims.errors import AgentCrashed
from agentpostmortem.toolbox import Toolbox

from . import INSTALL_HINT, FrameworkNotInstalledError
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

FRAMEWORK = "claude-agent-sdk"
PACKAGE = "claude-agent-sdk"
MODULE = "claude_agent_sdk"
CLI_ENV = "APM_CLAUDE_CLI"
SERVER_NAME = "apm"
TOOL_PREFIX = f"mcp__{SERVER_NAME}__"
API_KEY = "agentpostmortem-local"
RUN_TIMEOUT_SECONDS = 300.0
PERSIST_TIMEOUT_SECONDS = 5.0
STORE_KEY = "claude_agent.session"
SDK_HINT = f"adapter needs the Claude Agent SDK ({PACKAGE}). Run: {INSTALL_HINT}"
CLI_HINT = (
    "the Claude Agent SDK found no Claude Code CLI: install a claude-agent-sdk wheel that "
    f"bundles it, put `claude` on PATH, or set {CLI_ENV} to its path"
)

_JSON_TYPES = {
    bool: "boolean",
    int: "integer",
    float: "number",
    str: "string",
    type(None): "null",
}


# Locating the SDK and the CLI it drives.


def _sdk() -> Any:
    """The ``claude_agent_sdk`` module, imported only when a run needs it."""
    return importlib.import_module(MODULE)


def sdk_installed() -> bool:
    return find_spec(MODULE) is not None


def bundled_cli() -> Path | None:
    """The CLI shipped inside the SDK wheel, which the SDK uses before ``PATH``."""
    spec = find_spec(MODULE)
    if spec is None or not spec.submodule_search_locations:
        return None
    name = "claude.exe" if os.name == "nt" else "claude"
    for location in spec.submodule_search_locations:
        candidate = Path(location) / "_bundled" / name
        if candidate.is_file():
            return candidate
    return None


def locate_cli() -> Path:
    """The Claude Code CLI the SDK would run, or an explanation of what is missing."""
    override = os.environ.get(CLI_ENV, "")
    if override:
        path = Path(override).expanduser()
        if not path.is_file():
            raise FrameworkNotInstalledError(f"{CLI_ENV}={path} is not a file")
        return path
    found = bundled_cli()
    if found is not None:
        return found
    on_path = shutil.which("claude")
    if on_path is None:
        raise FrameworkNotInstalledError(CLI_HINT)
    return Path(on_path)


def is_available() -> bool:
    if not sdk_installed():
        return False
    try:
        locate_cli()
    except FrameworkNotInstalledError:
        return False
    return True


@cache
def cli_version(cli: Path) -> str:
    """``claude --version`` run in an empty home, as ``2.1.327``."""
    try:
        with tempfile.TemporaryDirectory(prefix="apm-claude-version-") as home:
            out = subprocess.run(
                [str(cli), "--version"],
                capture_output=True,
                text=True,
                check=True,
                timeout=60,
                env={
                    "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                    "HOME": home,
                    "CLAUDE_CONFIG_DIR": str(Path(home) / ".claude"),
                    **_QUIET_ENV,
                },
            )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return out.stdout.split(" ", 1)[0].strip() or "unknown"


def framework_version(cli: Path) -> str:
    try:
        sdk = version(PACKAGE)
    except PackageNotFoundError:
        sdk = "unknown"
    return f"claude-agent-sdk {sdk} (Claude Code {cli_version(cli)})"


# The conversation, as Claude Code sends it in Messages API format.


def _blocks(content: Any) -> list[Mapping[str, Any]]:
    if isinstance(content, list):
        return [block for block in content if isinstance(block, Mapping)]
    return []


def result_text(block: Mapping[str, Any]) -> str:
    """A ``tool_result`` block as the script reads it.

    The SDK reports a tool that raised as ``is_error: true`` with the exception
    message as content, so that is an error, not a result.
    """
    content = block.get("content")
    if isinstance(content, str):
        text = content
    else:
        text = "".join(str(b.get("text", "")) for b in _blocks(content) if b.get("type") == "text")
    if block.get("is_error"):
        return f"Error: {text}"
    try:
        value = json.loads(text)
    except ValueError:
        return text
    return encode_result(value) if isinstance(value, dict) else text


def observed(messages: Sequence[Mapping[str, Any]]) -> list[Observed]:
    """Every tool call in a Messages API conversation, with its result if present."""
    results = {
        str(block.get("tool_use_id")): result_text(block)
        for message in messages
        if message.get("role") == "user"
        for block in _blocks(message.get("content"))
        if block.get("type") == "tool_result"
    }
    return [
        Observed(str(block.get("id")), results.get(str(block.get("id"))))
        for message in messages
        if message.get("role") == "assistant"
        for block in _blocks(message.get("content"))
        if block.get("type") == "tool_use"
    ]


def ends_with_result(messages: Sequence[Mapping[str, Any]]) -> bool:
    """Whether the last user or assistant turn hands tool results to the model.

    Claude Code may add ``system`` messages (environment notes) after a turn;
    they do not change who spoke last.
    """
    for message in reversed(messages):
        if message.get("role") == "system":
            continue
        return message.get("role") == "user" and any(
            block.get("type") == "tool_result" for block in _blocks(message.get("content"))
        )
    return False


def input_schema(spec: ToolSpec) -> dict[str, Any]:
    """The JSON schema of one plan tool, given to ``@tool``."""
    return {
        "type": "object",
        "properties": {arg.name: _arg_schema(arg) for arg in spec.args},
        "required": [arg.name for arg in spec.args if arg.required],
    }


def _arg_schema(arg: ArgSpec) -> dict[str, Any]:
    return _json_schema(arg.types)


def _json_schema(kinds: Sequence[Any]) -> dict[str, Any]:
    """JSON Schema for annotations from the plan: scalars, unions, ``list[...]``, TypedDicts."""
    flat: list[Any] = []
    for kind in kinds:
        if typing.get_origin(kind) in (typing.Union, types.UnionType):
            flat.extend(typing.get_args(kind))
        else:
            flat.append(kind)
    schemas = [_one_schema(kind) for kind in flat]
    if len(schemas) == 1:
        return schemas[0]
    if all(set(schema) == {"type"} for schema in schemas):
        return {"type": [schema["type"] for schema in schemas]}
    return {"anyOf": schemas}


def _one_schema(kind: Any) -> dict[str, Any]:
    if typing.get_origin(kind) is list:
        items = typing.get_args(kind)
        return {"type": "array", "items": _json_schema(items)} if items else {"type": "array"}
    if typing_extensions.is_typeddict(kind):
        hints = typing.get_type_hints(kind)
        return {
            "type": "object",
            "properties": {name: _json_schema((hint,)) for name, hint in hints.items()},
            "required": sorted(getattr(kind, "__required_keys__", hints)),
        }
    return {"type": _JSON_TYPES.get(kind, "string")}


def _content(action: Action) -> tuple[list[dict[str, Any]], str]:
    if isinstance(action, FinalAnswer):
        return [{"type": "text", "text": action.text}], "end_turn"
    block = {
        "type": "tool_use",
        "id": action.call_id,
        "name": TOOL_PREFIX + action.name,
        "input": action.arguments,
    }
    return [block], "tool_use"


def _events(message: Mapping[str, Any], content: Sequence[Mapping[str, Any]]) -> bytes:
    """A Messages API server-sent event stream for one finished message."""
    events: list[dict[str, Any]] = [
        {"type": "message_start", "message": {**message, "content": [], "stop_reason": None}}
    ]
    for index, block in enumerate(content):
        if block["type"] == "tool_use":
            start = {**block, "input": {}}
            delta = {"type": "input_json_delta", "partial_json": json.dumps(block["input"])}
        else:
            start = {"type": "text", "text": ""}
            delta = {"type": "text_delta", "text": block["text"]}
        events += [
            {"type": "content_block_start", "index": index, "content_block": start},
            {"type": "content_block_delta", "index": index, "delta": delta},
            {"type": "content_block_stop", "index": index},
        ]
    events += [
        {
            "type": "message_delta",
            "delta": {"stop_reason": message["stop_reason"], "stop_sequence": None},
            "usage": {"output_tokens": 0},
        },
        {"type": "message_stop"},
    ]
    return b"".join(
        f"event: {event['type']}\ndata: {json.dumps(event)}\n\n".encode() for event in events
    )


# Claude Code's session transcript, as the scripted model's pacing reads it.


def persisted(projects: Path) -> tuple[set[str], set[str], int]:
    """Tool calls, tool results and prompts in the main-thread session transcripts."""
    calls: set[str] = set()
    results: set[str] = set()
    prompts = 0
    for path in sorted(projects.glob("*/*.jsonl")):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                entry = json.loads(line)
            except ValueError:
                continue  # a line still being written
            if not isinstance(entry, dict) or entry.get("isSidechain"):
                continue
            kind = entry.get("type")
            content = (entry.get("message") or {}).get("content")
            if kind == "assistant":
                calls.update(
                    str(b.get("id")) for b in _blocks(content) if b.get("type") == "tool_use"
                )
            elif kind == "user" and isinstance(content, str):
                prompts += 1
            elif kind == "user":
                found = [
                    str(b.get("tool_use_id"))
                    for b in _blocks(content)
                    if b.get("type") == "tool_result"
                ]
                results.update(found)
                prompts += not found
    return calls, results, prompts


def requested(messages: Sequence[Mapping[str, Any]]) -> tuple[set[str], set[str], int]:
    """The same three things in the conversation of a model request."""
    calls = {o.call_id for o in observed(messages)}
    results = {o.call_id for o in observed(messages) if o.result is not None}
    prompts = sum(
        1
        for m in messages
        if m.get("role") == "user"
        and not any(b.get("type") == "tool_result" for b in _blocks(m.get("content")))
    )
    return calls, results, prompts


def wait_until_persisted(
    projects: Path, messages: Sequence[Mapping[str, Any]], stopped: threading.Event
) -> bool:
    """Hold the model's answer until Claude Code has written the conversation to disk.

    Claude Code appends to its session transcript in the background, tens of
    milliseconds after it sends the request that contains the new entries. A real
    model takes far longer than that to answer, so by the time a real answer
    arrives the transcript holds the whole conversation it was asked about. The
    scripted model answers in microseconds, so it waits for the same state
    instead of a fixed delay. It never waits on the request where a crash fires:
    the CLI is killed while that request's new entries are still unwritten,
    exactly as they would be with a real model.
    """
    want_calls, want_results, want_prompts = requested(messages)
    deadline = time.monotonic() + PERSIST_TIMEOUT_SECONDS
    while not stopped.is_set() and time.monotonic() < deadline:
        calls, results, prompts = persisted(projects)
        if want_calls <= calls and want_results <= results and prompts >= want_prompts:
            return True
        stopped.wait(0.005)
    return False


# The local endpoint Claude Code talks to.


@dataclass
class _Session:
    """State shared by the endpoint threads, the in-process tools and the run."""

    script: PlanScript
    tools: Toolbox
    specs: dict[str, ToolSpec]
    pid_file: Path | None = None
    projects: Path | None = None
    """Where Claude Code writes session transcripts (``CLAUDE_CONFIG_DIR/projects``)."""
    lock: threading.Lock = field(default_factory=threading.Lock)
    stopped: threading.Event = field(default_factory=threading.Event)
    pending: dict[str, deque[str]] = field(default_factory=dict)
    """Call ids the model issued per tool, oldest first, not yet run by a tool."""
    conversation: list[Mapping[str, Any]] = field(default_factory=list)
    """The conversation of the latest agent turn."""
    crash: AgentCrashed | None = None
    failure: str | None = None
    blocked: list[str] = field(default_factory=list)
    turns: int = 0
    other_requests: int = 0

    def kill_cli(self) -> None:
        """Kill the CLI process where it stands, as a crash would."""
        if self.pid_file is None:
            return
        try:
            pid = int(self.pid_file.read_text(encoding="ascii").strip())
            os.kill(pid, signal.SIGKILL)
        except (OSError, ValueError):
            return


class _Handler(BaseHTTPRequestHandler):
    server: _Server

    def log_message(self, format: str, *args: Any) -> None:
        return

    def do_CONNECT(self) -> None:
        # A proxied HTTPS request for another host: refuse it and fail the run.
        self._block(self.path)

    def do_GET(self) -> None:
        if self.path.startswith("http"):
            self._block(self.path)
        else:
            self._json(404, {"type": "error", "error": {"type": "not_found_error"}})

    def do_POST(self) -> None:
        if self.path.startswith("http"):
            self._block(self.path)
            return
        session = self.server.session
        length = int(self.headers.get("Content-Length") or 0)
        try:
            request = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            self._error(400, "invalid_request_error", "invalid JSON")
            return
        path = self.path.split("?", 1)[0].rstrip("/")
        if session.stopped.is_set():
            self._error(503, "overloaded_error", "the run has ended")
        elif path == "/v1/messages/count_tokens":
            self._json(200, {"input_tokens": 0})
        elif path == "/v1/messages":
            self._messages(session, request)
        else:
            self._error(404, "not_found_error", f"no route for {path}")

    def _block(self, target: str) -> None:
        session = self.server.session
        with session.lock:
            session.blocked.append(f"{self.command} {target}")
        self._error(403, "permission_error", "network access is disabled in framework mode")

    def _messages(self, session: _Session, request: Mapping[str, Any]) -> None:
        offered = {str(tool.get("name")) for tool in request.get("tools") or ()}
        messages: list[Mapping[str, Any]] = list(request.get("messages") or ())
        if not offered & {TOOL_PREFIX + name for name in session.specs}:
            # A side request (for example a title or summary), not the agent's turn.
            with session.lock:
                session.other_requests += 1
            self._reply(request, [{"type": "text", "text": "OK"}], "end_turn")
            return
        with session.lock:
            session.turns += 1
            session.conversation = messages
            try:
                action = session.script.next_action(
                    observed(messages), last_is_result=ends_with_result(messages)
                )
            except AgentCrashed as crash:
                # No answer: the CLI is killed now, and every later request is refused.
                session.crash = crash
                session.stopped.set()
                session.kill_cli()
                return
            except Exception as error:
                session.failure = f"The scripted model failed: {type(error).__name__}: {error}"
                session.stopped.set()
                self._error(500, "api_error", session.failure)
                return
            if not isinstance(action, FinalAnswer):
                session.pending.setdefault(action.name, deque()).append(action.call_id)
        if session.projects is not None:
            wait_until_persisted(session.projects, messages, session.stopped)
        content, stop = _content(action)
        self._reply(request, content, stop)

    def _reply(self, request: Mapping[str, Any], content: list[dict[str, Any]], stop: str) -> None:
        session = self.server.session
        message = {
            "id": f"msg_apm_{session.turns:04d}",
            "type": "message",
            "role": "assistant",
            "model": str(request.get("model") or "agentpostmortem-scripted"),
            "content": content,
            "stop_reason": stop,
            "stop_sequence": None,
            "usage": {"input_tokens": 0, "output_tokens": 0},
        }
        if not request.get("stream"):
            self._json(200, message)
            return
        body = _events(message, content)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _error(self, status: int, kind: str, message: str) -> None:
        self._json(status, {"type": "error", "error": {"type": kind, "message": message}})

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


# The in-process tools.


def run_plan_tool(session: _Session, spec: ToolSpec, arguments: Mapping[str, Any]) -> str:
    """The body of every SDK tool: the toolbox call for the step the model issued."""
    with session.lock:
        if session.stopped.is_set():
            raise RuntimeError("the run has ended")
        queue = session.pending.get(spec.name)
        if not queue:
            session.failure = f"Claude Code ran {spec.name} without a model call for it"
            session.stopped.set()
            raise RuntimeError(session.failure)
        step = session.script.step_for(queue.popleft())
        return call_tool(session.tools, step, spec.service, spec.operation, arguments)


def build_server(session: _Session, specs: Iterable[ToolSpec]) -> Any:
    """An SDK MCP server with one ``@tool`` per plan operation."""
    sdk = _sdk()

    def make(spec: ToolSpec) -> Any:
        async def handler(args: dict[str, Any]) -> dict[str, Any]:
            text = run_plan_tool(session, spec, args)
            return {"content": [{"type": "text", "text": text}]}

        return sdk.tool(spec.name, spec.description, input_schema(spec))(handler)

    return sdk.create_sdk_mcp_server(SERVER_NAME, tools=[make(spec) for spec in specs])


# The run.

# Documented Claude Code switches that keep a run off the network and quiet.
_QUIET_ENV = {
    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
    "DISABLE_TELEMETRY": "1",
    "DISABLE_ERROR_REPORTING": "1",
    "DISABLE_AUTOUPDATER": "1",
    "LANG": "C.UTF-8",
    "TZ": "UTC",
}


def run_env(workdir: Path, endpoint: str) -> dict[str, str]:
    """The CLI's environment: every inherited variable blanked, then the run's own."""
    home = workdir / "home"
    env = dict.fromkeys(os.environ, "")
    env.update(_QUIET_ENV)
    env.update(
        {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": str(home),
            "CLAUDE_CONFIG_DIR": str(home / ".claude"),
            "TMPDIR": str(workdir / "tmp"),
            "ANTHROPIC_BASE_URL": endpoint,
            "ANTHROPIC_API_KEY": API_KEY,
            "HTTP_PROXY": endpoint,
            "HTTPS_PROXY": endpoint,
            "NO_PROXY": "127.0.0.1,localhost",
        }
    )
    return env


def write_wrapper(workdir: Path, cli: Path) -> tuple[Path, Path]:
    """A launcher that records the CLI's process id, then ``exec``s the CLI."""
    pid_file = workdir / "cli.pid"
    wrapper = workdir / "claude"
    wrapper.write_text(
        f'#!/bin/sh\necho $$ > {shlex.quote(str(pid_file))}\nexec {shlex.quote(str(cli))} "$@"\n',
        encoding="utf-8",
    )
    wrapper.chmod(0o755)
    return wrapper, pid_file


@dataclass
class _Durable:
    """What the resume variant keeps across restarts, like a disk would."""

    directory: tempfile.TemporaryDirectory[str]
    session_id: str


@dataclass(frozen=True)
class ClaudeAgentVariant:
    resume: bool = False


class ClaudeAgentSDKAgent:
    """Runs a scenario through ``claude_agent_sdk.query`` with a scripted model endpoint."""

    framework = FRAMEWORK

    def __init__(self, name: str, options: ClaudeAgentVariant, cli: Path) -> None:
        self.name = name
        self.options = options
        self.cli = cli
        self.framework_version = framework_version(cli)

    def run(self, scenario: Scenario, tools: Toolbox, store: CheckpointStore) -> AgentOutcome:
        script = PlanScript(scenario, tools)
        specs = tool_specs(scenario)
        session = _Session(script, tools, {spec.name: spec for spec in specs})
        server = _Server(session)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        durable: _Durable | None = store.get(STORE_KEY) if self.options.resume else None
        resume_id = durable.session_id if durable is not None else None
        if durable is None:
            durable = _Durable(tempfile.TemporaryDirectory(prefix="apm-claude-"), str(uuid.uuid4()))
            if self.options.resume:
                store.put(STORE_KEY, durable)
        try:
            return self._run(
                scenario=scenario,
                session=session,
                server=server,
                specs=specs,
                durable=durable,
                resume_id=resume_id,
            )
        finally:
            session.stopped.set()
            server.shutdown()
            server.server_close()
            thread.join()
            if not self.options.resume:
                durable.directory.cleanup()

    def _run(
        self,
        *,
        scenario: Scenario,
        session: _Session,
        server: _Server,
        specs: Sequence[ToolSpec],
        durable: _Durable,
        resume_id: str | None,
    ) -> AgentOutcome:
        workdir = Path(durable.directory.name)
        for sub in ("home", "project", "tmp"):
            (workdir / sub).mkdir(exist_ok=True)
        wrapper, session.pid_file = write_wrapper(workdir, self.cli)
        session.projects = workdir / "home" / ".claude" / "projects"
        endpoint = f"http://127.0.0.1:{server.server_address[1]}"
        stderr: deque[str] = deque(maxlen=20)
        try:
            result = asyncio.run(
                _drive(
                    durable,
                    scenario.task,
                    _options(
                        session=session,
                        specs=specs,
                        workdir=workdir,
                        wrapper=wrapper,
                        endpoint=endpoint,
                        stderr=stderr,
                        session_id=durable.session_id,
                        resume_id=resume_id,
                    ),
                )
            )
        except Exception as error:
            if session.crash is not None:
                raise session.crash from None
            if session.failure is not None:
                raise RuntimeError(session.failure) from error
            return AgentOutcome(
                Status.FAILED,
                f"The agent run raised {type(error).__name__}: {error}",
                session.script.completed_steps(observed(session.conversation)),
            )
        if session.crash is not None:
            raise session.crash
        if session.failure is not None:
            raise RuntimeError(f"{session.failure}\n" + "\n".join(stderr))
        if session.blocked:
            raise RuntimeError(f"Claude Code tried to reach the network: {session.blocked}")
        completed = session.script.completed_steps(observed(session.conversation))
        if result is None:
            return AgentOutcome(Status.FAILED, "Claude Code ended without a result.", completed)
        claim = str(getattr(result, "result", None) or "")
        if getattr(result, "is_error", False) or getattr(result, "subtype", "") != "success":
            reason = claim or str(getattr(result, "subtype", "error"))
            return AgentOutcome(Status.FAILED, f"The Claude Code run failed: {reason}", completed)
        return AgentOutcome(Status.COMPLETED, claim, completed)


def _options(
    *,
    session: _Session,
    specs: Sequence[ToolSpec],
    workdir: Path,
    wrapper: Path,
    endpoint: str,
    stderr: deque[str],
    session_id: str,
    resume_id: str | None,
) -> Any:
    return _sdk().ClaudeAgentOptions(
        tools=[],
        mcp_servers={SERVER_NAME: build_server(session, specs)},
        allowed_tools=[TOOL_PREFIX + spec.name for spec in specs],
        strict_mcp_config=True,
        setting_sources=[],
        cwd=workdir / "project",
        cli_path=wrapper,
        env=run_env(workdir, endpoint),
        stderr=stderr.append,
        session_id=None if resume_id else session_id,
        resume=resume_id,
    )


async def _drive(durable: _Durable, prompt: str, options: Any) -> Any:
    """Run one ``query`` to its end and return its ``ResultMessage``, if any.

    The session id the CLI reports at start is kept, for a later ``resume``.
    """
    sdk = _sdk()
    result: Any = None
    async with asyncio.timeout(RUN_TIMEOUT_SECONDS):
        async for message in sdk.query(prompt=prompt, options=options):
            if isinstance(message, sdk.SystemMessage) and message.subtype == "init":
                durable.session_id = str(message.data.get("session_id") or durable.session_id)
            elif isinstance(message, sdk.ResultMessage):
                result = message
    return result


VARIANTS: dict[str, ClaudeAgentVariant] = {
    "claude-agent-sdk": ClaudeAgentVariant(),
    "claude-agent-sdk-resume": ClaudeAgentVariant(resume=True),
}


def make(name: str) -> ClaudeAgentSDKAgent:
    if name not in VARIANTS:
        raise KeyError(name)
    if not sdk_installed():
        raise FrameworkNotInstalledError(f"adapter {name!r} {SDK_HINT}")
    return ClaudeAgentSDKAgent(name, VARIANTS[name], locate_cli())

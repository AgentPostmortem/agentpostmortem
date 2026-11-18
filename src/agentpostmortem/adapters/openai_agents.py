"""OpenAI Agents SDK agents in framework mode.

The agent is a plain ``agents.Agent`` run by ``Runner`` with the SDK's own tool
loop and ``function_tool`` wrappers. Only the model is replaced: a ``Model``
implementation that returns function calls taken from the scenario plan, then a
final message. Tracing is disabled so nothing tries to export spans over the
network; it does not change how tools or errors are handled.

Variants change only settings the SDK documents:

* ``openai-agents``: defaults. ``function_tool`` turns a tool exception into the
  fixed message "An error occurred while running the tool." for the model.
* ``openai-agents-raise``: ``failure_error_function=None``, the documented way to
  make tool exceptions fail the run instead.
* ``openai-agents-session``: a ``SQLiteSession`` kept across restarts, the SDK's
  documented way to persist conversation history between runs.
"""

from __future__ import annotations

import inspect
import json
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from importlib.metadata import version
from typing import Any

from agents import (
    Agent,
    FunctionTool,
    Model,
    ModelResponse,
    ModelSettings,
    ModelTracing,
    RunConfig,
    Runner,
    SQLiteSession,
    Tool,
    Usage,
    function_tool,
)
from agents.agent_output import AgentOutputSchemaBase
from agents.handoffs import Handoff
from agents.items import TResponseInputItem, TResponseOutputItem, TResponseStreamEvent
from agents.tool_context import ToolContext
from openai.types.responses import (
    ResponseFunctionToolCall,
    ResponseOutputMessage,
    ResponseOutputText,
)
from openai.types.responses.response_prompt_param import ResponsePromptParam
from typing_extensions import override

from agentpostmortem.agents.base import CheckpointStore
from agentpostmortem.domain.outcome import AgentOutcome, Status
from agentpostmortem.domain.scenario import Scenario
from agentpostmortem.toolbox import Toolbox

from ._script import FinalAnswer, Observed, PlanScript, ToolSpec, call_tool, tool_specs

FRAMEWORK = "openai-agents"
SESSION_KEY = "openai_agents.session"


def framework_version() -> str:
    return f"openai-agents {version('openai-agents')}"


def _field(item: Any, key: str) -> Any:
    return item.get(key) if isinstance(item, dict) else getattr(item, key, None)


def observed(items: str | Sequence[Any]) -> list[Observed]:
    """Every function call in a Responses-format conversation, with its output if present."""
    if isinstance(items, str):
        return []
    outputs = {
        str(_field(i, "call_id")): str(_field(i, "output"))
        for i in items
        if _field(i, "type") == "function_call_output"
    }
    return [
        Observed(str(_field(i, "call_id")), outputs.get(str(_field(i, "call_id"))))
        for i in items
        if _field(i, "type") == "function_call"
    ]


class ScriptedModel(Model):
    """A ``Model`` that emits the scenario plan as function calls, then a final message."""

    def __init__(self, script: PlanScript) -> None:
        self.script = script
        self._turn = 0

    @override
    async def get_response(
        self,
        system_instructions: str | None,
        input: str | list[TResponseInputItem],
        model_settings: ModelSettings,
        tools: list[Tool],
        output_schema: AgentOutputSchemaBase | None,
        handoffs: list[Handoff],
        tracing: ModelTracing,
        *,
        previous_response_id: str | None,
        conversation_id: str | None,
        prompt: ResponsePromptParam | None,
    ) -> ModelResponse:
        self._turn += 1
        last_is_result = (
            not isinstance(input, str)
            and bool(input)
            and _field(input[-1], "type") == "function_call_output"
        )
        action = self.script.next_action(observed(input), last_is_result=last_is_result)
        output: TResponseOutputItem
        if isinstance(action, FinalAnswer):
            output = ResponseOutputMessage(
                id=f"msg_apm_{self._turn}",
                role="assistant",
                status="completed",
                type="message",
                content=[ResponseOutputText(text=action.text, type="output_text", annotations=[])],
            )
        else:
            output = ResponseFunctionToolCall(
                id=f"fc_apm_{self._turn}",
                call_id=action.call_id,
                name=action.name,
                arguments=json.dumps(action.arguments, sort_keys=True),
                type="function_call",
                status="completed",
            )
        return ModelResponse(output=[output], usage=Usage(), response_id=None)

    @override
    async def stream_response(
        self,
        system_instructions: str | None,
        input: str | list[TResponseInputItem],
        model_settings: ModelSettings,
        tools: list[Tool],
        output_schema: AgentOutputSchemaBase | None,
        handoffs: list[Handoff],
        tracing: ModelTracing,
        *,
        previous_response_id: str | None,
        conversation_id: str | None,
        prompt: ResponsePromptParam | None,
    ) -> AsyncIterator[TResponseStreamEvent]:
        raise NotImplementedError("framework mode runs without streaming")
        yield  # pragma: no cover


def build_tools(script: PlanScript, tools: Toolbox, *, raise_errors: bool) -> list[FunctionTool]:
    return [_tool(spec, script, tools, raise_errors) for spec in tool_specs(script.scenario)]


def _tool(spec: ToolSpec, script: PlanScript, tools: Toolbox, raise_errors: bool) -> FunctionTool:
    def invoke(ctx: ToolContext[Any], **kwargs: Any) -> str:
        step = script.step_for(ctx.tool_call_id)
        return call_tool(tools, step, spec.service, spec.operation, kwargs)

    # Give the function the plan's argument list so the SDK derives a real JSON schema.
    parameters = [
        inspect.Parameter(
            "ctx", inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=ToolContext[Any]
        )
    ]
    annotations: dict[str, Any] = {"ctx": ToolContext[Any], "return": str}
    for arg in spec.args:
        kind: Any = arg.types[0]
        for extra in arg.types[1:]:
            kind = kind | extra
        if not arg.required:
            kind = kind | None
        default = inspect.Parameter.empty if arg.required else None
        parameters.append(
            inspect.Parameter(
                arg.name, inspect.Parameter.KEYWORD_ONLY, annotation=kind, default=default
            )
        )
        annotations[arg.name] = kind
    invoke.__signature__ = inspect.Signature(parameters, return_annotation=str)  # type: ignore[attr-defined]
    invoke.__annotations__ = annotations
    invoke.__name__ = spec.name
    invoke.__doc__ = spec.description
    if raise_errors:
        return function_tool(invoke, failure_error_function=None)
    return function_tool(invoke)


@dataclass(frozen=True)
class OpenAIAgentsOptions:
    raise_errors: bool = False
    session: bool = False


class OpenAIAgentsAgent:
    """Runs a scenario through an OpenAI Agents SDK ``Agent`` and ``Runner``."""

    framework = FRAMEWORK

    def __init__(self, name: str, options: OpenAIAgentsOptions) -> None:
        self.name = name
        self.options = options
        self.framework_version = framework_version()

    def run(self, scenario: Scenario, tools: Toolbox, store: CheckpointStore) -> AgentOutcome:
        script = PlanScript(scenario, tools)
        agent: Agent[Any] = Agent(
            name="assistant",
            instructions="Complete the user's task with the tools.",
            model=ScriptedModel(script),
            tools=list(build_tools(script, tools, raise_errors=self.options.raise_errors)),
        )
        session: SQLiteSession | None = None
        if self.options.session:
            # The session lives in the durable store, as its database would outlive the process.
            session = store.get(SESSION_KEY)
            if session is None:
                session = SQLiteSession(f"{scenario.id}-{self.name}")
                store.put(SESSION_KEY, session)
        try:
            result = Runner.run_sync(
                agent,
                scenario.task,
                run_config=RunConfig(tracing_disabled=True),
                session=session,
            )
        except Exception as error:
            return AgentOutcome(
                Status.FAILED, f"The agent run raised {type(error).__name__}: {error}"
            )
        history = observed(result.to_input_list())
        return AgentOutcome(
            Status.COMPLETED, str(result.final_output), script.completed_steps(history)
        )


VARIANTS: dict[str, OpenAIAgentsOptions] = {
    "openai-agents": OpenAIAgentsOptions(),
    "openai-agents-raise": OpenAIAgentsOptions(raise_errors=True),
    "openai-agents-session": OpenAIAgentsOptions(session=True),
}


def make(name: str) -> OpenAIAgentsAgent:
    return OpenAIAgentsAgent(name, VARIANTS[name])

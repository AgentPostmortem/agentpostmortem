"""LangGraph agents in framework mode.

The agent is built with ``langchain.agents.create_agent``, the current home of
LangGraph's prebuilt ReAct agent (``langgraph.prebuilt.create_react_agent`` is
deprecated in its favour since LangGraph 1.0). It runs on the LangGraph runtime
with the framework's own ``ToolNode``. Only the chat model is replaced: a
``BaseChatModel`` subclass that answers with ``AIMessage`` tool calls taken from
the scenario plan.

Variants change only settings the framework documents:

* ``langgraph``: defaults. ``ToolNode`` lets tool exceptions propagate, so the
  graph run raises.
* ``langgraph-checkpointer``: a checkpointer and a thread id, LangGraph's
  documented durable setup. After a crash the run resumes the thread with
  ``invoke(None, config)``.
* ``langgraph-tool-retry``: ``ToolRetryMiddleware`` with its default retry count
  and failure handling. Backoff delays are set to zero because the simulated
  services run on a logical clock; delays would only slow the test down.
* ``langgraph-tool-errors``: ``ToolErrorMiddleware`` that hands every tool
  exception back to the model as an error ``ToolMessage``.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from importlib.metadata import version
from typing import Annotated, Any

from langchain.agents import create_agent
from langchain.agents.middleware import (
    AgentMiddleware,
    ToolCallRequest,
    ToolErrorMiddleware,
    ToolRetryMiddleware,
)
from langchain.agents.middleware.types import InputAgentState
from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel, LanguageModelInput
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import Runnable, RunnableConfig
from langchain_core.tools import BaseTool, InjectedToolCallId, StructuredTool
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import BaseModel, PrivateAttr, create_model

from agentpostmortem.agents.base import CheckpointStore
from agentpostmortem.domain.outcome import AgentOutcome, Status
from agentpostmortem.domain.scenario import Scenario
from agentpostmortem.toolbox import Toolbox

from ._script import (
    FinalAnswer,
    Observed,
    PlanScript,
    ToolSpec,
    call_tool,
    tool_specs,
)

FRAMEWORK = "langgraph"
CHECKPOINTER_KEY = "langgraph.checkpointer"


def framework_version() -> str:
    return f"langgraph {version('langgraph')}, langchain {version('langchain')}"


class ScriptedChatModel(BaseChatModel):
    """A chat model that emits the scenario plan as tool calls, then a final answer."""

    _script: PlanScript = PrivateAttr()

    def __init__(self, script: PlanScript, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._script = script

    @property
    def _llm_type(self) -> str:
        return "agentpostmortem-scripted"

    def bind_tools(
        self,
        tools: Sequence[dict[str, Any] | type | Callable[..., Any] | BaseTool],
        *,
        tool_choice: str | None = None,
        **kwargs: Any,
    ) -> Runnable[LanguageModelInput, AIMessage]:
        # The script already knows which tool it calls next; binding changes nothing.
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        history = observed(messages)
        last_is_result = bool(messages) and isinstance(messages[-1], ToolMessage)
        action = self._script.next_action(history, last_is_result=last_is_result)
        if isinstance(action, FinalAnswer):
            message = AIMessage(content=action.text)
        else:
            message = AIMessage(
                content="",
                tool_calls=[{"name": action.name, "args": action.arguments, "id": action.call_id}],
            )
        return ChatResult(generations=[ChatGeneration(message=message)])


def observed(messages: Sequence[BaseMessage]) -> list[Observed]:
    """Every tool call in a LangChain conversation, with its result if present."""
    results = {m.tool_call_id: str(m.text) for m in messages if isinstance(m, ToolMessage)}
    calls: list[Observed] = []
    for message in messages:
        if isinstance(message, AIMessage):
            for call in message.tool_calls:
                identifier = call["id"] or ""
                calls.append(Observed(identifier, results.get(identifier)))
    return calls


def build_tools(script: PlanScript, tools: Toolbox) -> list[BaseTool]:
    return [_tool(spec, script, tools) for spec in tool_specs(script.scenario)]


def _tool(spec: ToolSpec, script: PlanScript, tools: Toolbox) -> BaseTool:
    fields: dict[str, Any] = {}
    for arg in spec.args:
        kind: Any = arg.types[0] if len(arg.types) == 1 else _union(arg.types)
        fields[arg.name] = (kind, ...) if arg.required else (kind | None, None)
    fields["tool_call_id"] = (Annotated[str, InjectedToolCallId], ...)
    schema: type[BaseModel] = create_model(f"{spec.name}_args", **fields)

    def invoke(tool_call_id: str, **kwargs: Any) -> str:
        step = script.step_for(tool_call_id)
        return call_tool(tools, step, spec.service, spec.operation, kwargs)

    return StructuredTool.from_function(
        func=invoke, name=spec.name, description=spec.description, args_schema=schema
    )


def _union(types: tuple[type, ...]) -> Any:
    result: Any = types[0]
    for kind in types[1:]:
        result = result | kind
    return result


@dataclass(frozen=True)
class LangGraphOptions:
    checkpointer: bool = False
    tool_retry: bool = False
    tool_errors: bool = False


def _surface_every_error(error: Exception, request: ToolCallRequest) -> str:
    return f"Error: {type(error).__name__}: {error}"


class LangGraphAgent:
    """Runs a scenario through a LangGraph ``create_agent`` graph."""

    framework = FRAMEWORK

    def __init__(self, name: str, options: LangGraphOptions) -> None:
        self.name = name
        self.options = options
        self.framework_version = framework_version()

    def _middleware(self) -> list[AgentMiddleware[Any, Any, Any]]:
        middleware: list[AgentMiddleware[Any, Any, Any]] = []
        if self.options.tool_errors:
            middleware.append(ToolErrorMiddleware(on_error=_surface_every_error))
        if self.options.tool_retry:
            middleware.append(ToolRetryMiddleware(initial_delay=0.0, jitter=False))
        return middleware

    def run(self, scenario: Scenario, tools: Toolbox, store: CheckpointStore) -> AgentOutcome:
        script = PlanScript(scenario, tools)
        config: RunnableConfig = {"recursion_limit": 4 * len(scenario.plan) + 10}
        checkpointer: InMemorySaver | None = None
        if self.options.checkpointer:
            # The saver lives in the durable store, as a database would outlive the process.
            checkpointer = store.get(CHECKPOINTER_KEY)
            if checkpointer is None:
                checkpointer = InMemorySaver()
                store.put(CHECKPOINTER_KEY, checkpointer)
            config["configurable"] = {"thread_id": f"{scenario.id}-{self.name}"}
        graph = create_agent(
            ScriptedChatModel(script),
            build_tools(script, tools),
            middleware=self._middleware(),
            checkpointer=checkpointer,
        )
        resume = checkpointer is not None and bool(graph.get_state(config).next)
        payload: InputAgentState | None = (
            None if resume else {"messages": [HumanMessage(content=scenario.task)]}
        )
        try:
            state = graph.invoke(payload, config)
        except Exception as error:
            history = observed(_messages(graph, config)) if checkpointer else []
            return AgentOutcome(
                Status.FAILED,
                f"The agent run raised {type(error).__name__}: {error}",
                script.completed_steps(history),
            )
        messages: list[BaseMessage] = list(state["messages"])
        final = messages[-1] if messages else None
        claim = str(final.text) if isinstance(final, AIMessage) else ""
        return AgentOutcome(Status.COMPLETED, claim, script.completed_steps(observed(messages)))


def _messages(graph: Any, config: RunnableConfig) -> list[BaseMessage]:
    values = graph.get_state(config).values
    return list(values.get("messages", [])) if isinstance(values, dict) else []


VARIANTS: dict[str, LangGraphOptions] = {
    "langgraph": LangGraphOptions(),
    "langgraph-checkpointer": LangGraphOptions(checkpointer=True),
    "langgraph-tool-retry": LangGraphOptions(tool_retry=True),
    "langgraph-tool-errors": LangGraphOptions(tool_errors=True),
}


def make(name: str) -> LangGraphAgent:
    return LangGraphAgent(name, VARIANTS[name])

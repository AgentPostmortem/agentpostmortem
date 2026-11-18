"""The framework-neutral script that stands in for the model in framework mode.

A scripted model reads the conversation the framework hands it, the same way a
real model would, and answers with exactly one tool call per plan step, then a
final answer. It never retries, never adds idempotency keys and never judges a
tool result: whatever reliability a run shows comes from the framework.

The script also marks step boundaries for crash injection. A step counts as
finished when the framework hands its tool result back to the model, which is
the point where the framework has recorded the result in its own state (and, for
durable setups, checkpointed it). Killing the process there mirrors the
reference agents, which also crash only after they have saved their progress.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import cache
from typing import Any

from typing_extensions import TypedDict

from agentpostmortem.domain.scenario import Scenario, Step
from agentpostmortem.toolbox import Toolbox

CALL_ID_PREFIX = "call_apm_"


def call_id(step: Step) -> str:
    return f"{CALL_ID_PREFIX}{step.id}"


def tool_name(service: str, operation: str) -> str:
    return f"{service}_{operation}"


def encode_result(result: Mapping[str, Any]) -> str:
    """How a successful tool result is shown to the model: compact, stable JSON."""
    return json.dumps(dict(result), sort_keys=True, separators=(",", ":"))


def decode_result(text: str) -> dict[str, Any] | None:
    """Parse a tool result written by :func:`encode_result`; ``None`` for error text."""
    try:
        value = json.loads(text)
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


@dataclass(frozen=True)
class Observed:
    """One tool call the model already made, as seen in the conversation."""

    call_id: str
    result: str | None
    """The tool result text, or ``None`` if no result is in the conversation yet."""


@dataclass(frozen=True)
class ToolCallAction:
    step: Step
    call_id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class FinalAnswer:
    text: str


Action = ToolCallAction | FinalAnswer


@dataclass(frozen=True)
class ArgSpec:
    """One argument of a framework tool, inferred from the plan."""

    name: str
    types: tuple[Any, ...]
    """Python annotations the argument accepts: scalars, ``list[...]`` or a ``TypedDict``."""
    required: bool


@dataclass(frozen=True)
class ToolSpec:
    service: str
    operation: str
    args: tuple[ArgSpec, ...]

    @property
    def name(self) -> str:
        return tool_name(self.service, self.operation)

    @property
    def description(self) -> str:
        return f"Call the {self.operation} operation of the {self.service} service."


def tool_specs(scenario: Scenario) -> list[ToolSpec]:
    """One tool per service operation used by the plan, with arguments from the plan."""
    grouped: dict[tuple[str, str], list[Step]] = {}
    for step in scenario.plan:
        grouped.setdefault((step.service, step.operation), []).append(step)
    specs = []
    for (service, operation), steps in grouped.items():
        names: list[str] = []
        for step in steps:
            names.extend(k for k in step.args if k not in names)
        args = []
        for name in names:
            types: list[Any] = []
            for step in steps:
                if name in step.args:
                    for kind in _arg_types(step.args[name]):
                        if kind not in types:
                            types.append(kind)
            required = all(name in step.args for step in steps)
            args.append(ArgSpec(name, tuple(types), required))
        specs.append(ToolSpec(service, operation, tuple(args)))
    return specs


def _arg_types(value: Any) -> tuple[Any, ...]:
    """The annotations a plan value needs in a framework tool signature.

    Lists become ``list[...]`` of their element types and mappings become a
    ``TypedDict`` of their keys, so the framework derives a JSON schema that
    accepts the value and still validates its shape (including strict schemas,
    which need every array to name its items and every object its properties).
    """
    if isinstance(value, str) and value.startswith("$"):
        # A reference to an earlier result: its type is only known at run time.
        return (int, str)
    if value is None or isinstance(value, bool | int | float | str):
        return (type(value),)
    if isinstance(value, Mapping):
        return (_typed_dict(_shape(value)),)
    if isinstance(value, list | tuple):
        items: list[Any] = []
        for item in value:
            for kind in _arg_types(item):
                if kind not in items:
                    items.append(kind)
        element: Any = items[0] if items else str
        for kind in items[1:]:
            element = element | kind
        return (list[element],)
    return (str,)


def _shape(value: Mapping[Any, Any]) -> tuple[tuple[str, tuple[Any, ...]], ...]:
    return tuple((str(key), _arg_types(item)) for key, item in value.items())


@cache
def _typed_dict(shape: tuple[tuple[str, tuple[Any, ...]], ...]) -> Any:
    """One ``TypedDict`` per distinct mapping shape, so equal shapes share a type."""
    fields: dict[str, Any] = {}
    for key, kinds in shape:
        annotation: Any = kinds[0]
        for kind in kinds[1:]:
            annotation = annotation | kind
        fields[key] = annotation
    name = f"Mapping{_typed_dict.cache_info().currsize + 1}"
    return TypedDict(name, fields)  # type: ignore[operator]


class PlanScript:
    """Decides the next model turn from the conversation so far."""

    def __init__(self, scenario: Scenario, tools: Toolbox) -> None:
        self.scenario = scenario
        self.tools = tools
        self._by_call_id = {call_id(step): step for step in scenario.plan}

    def step_for(self, identifier: str) -> Step:
        if identifier not in self._by_call_id:
            raise KeyError(f"tool call {identifier!r} does not belong to the plan")
        return self._by_call_id[identifier]

    def next_action(self, history: Sequence[Observed], *, last_is_result: bool) -> Action:
        """Answer one model call.

        ``history`` lists every tool call visible in the conversation, oldest
        first. ``last_is_result`` says whether the conversation ends with a tool
        result, which is when a step has just finished.
        """
        if last_is_result and history:
            self.tools.finish_step(self.step_for(history[-1].call_id).id)
        called = {observed.call_id for observed in history}
        results = {
            self.step_for(o.call_id).id: decode_result(o.result)
            for o in history
            if o.result is not None
        }
        for step in self.scenario.plan:
            if call_id(step) in called:
                continue
            return ToolCallAction(
                step,
                call_id(step),
                tool_name(step.service, step.operation),
                _resolve(step, results),
            )
        return FinalAnswer("Done. I have completed the task.")

    def completed_steps(self, history: Sequence[Observed]) -> tuple[str, ...]:
        """Steps whose tool returned a well formed result, as the conversation shows."""
        return tuple(
            self.step_for(o.call_id).id
            for o in history
            if o.result is not None and decode_result(o.result) is not None
        )


def _resolve(step: Step, results: Mapping[str, dict[str, Any] | None]) -> dict[str, Any]:
    """Fill ``$step.field`` references from earlier results, as the reference agents do."""
    args: dict[str, Any] = {}
    for key, value in step.args.items():
        if isinstance(value, str) and value.startswith("$"):
            ref_step, _, field = value[1:].partition(".")
            earlier = results.get(ref_step) or {}
            args[key] = earlier.get(field, value)
        else:
            args[key] = value
    return args


def call_tool(
    tools: Toolbox, step: Step, service: str, operation: str, kwargs: Mapping[str, Any]
) -> str:
    """The body every framework tool runs: one toolbox call, no policy of its own."""
    arguments = {k: v for k, v in kwargs.items() if v is not None}
    return encode_result(tools.call(step.id, service, operation, **arguments))

"""Oracles for backoff, input validation, payload correctness, final state and cost."""

from __future__ import annotations

from collections import Counter
from typing import Any

from agentpostmortem.domain.effects import Effect
from agentpostmortem.domain.faults import FaultKind
from agentpostmortem.domain.outcome import ToolCall, Verdict
from agentpostmortem.oracles import Evidence, oracle
from agentpostmortem.sims.base import is_quota

_TYPES: dict[str, tuple[type, ...]] = {
    "str": (str,),
    "int": (int,),
    "float": (int, float),
    "bool": (bool,),
    "list": (list,),
    "dict": (dict,),
}


def _retry_after(ev: Evidence, call: ToolCall, number: int) -> int | None:
    """The wait a rate-limited call asked for, derived from the scenario's faults.

    For a quota (a ``rate_limit`` fault with ``limit`` and ``window``) it is
    the ticks left in the window at the time of the call; for a one-off
    ``rate_limit`` it is the fault's ``retry_after`` parameter.
    """
    for fault in ev.scenario.faults:
        if (
            fault.service == call.service
            and fault.operation == call.operation
            and fault.kind is FaultKind.RATE_LIMIT
            and fault.trigger.fires(number)
        ):
            if is_quota(fault):
                window = int(fault.params["window"])
                return window - call.tick % window
            return int(fault.params.get("retry_after", 5))
    return None


def _describe_call(call: ToolCall) -> str:
    return (
        f"call #{call.seq} {call.service}.{call.operation} step={call.step} "
        f"attempt={call.attempt} tick={call.tick} -> {call.result}"
    )


def _matches(call: ToolCall, params: dict[str, Any]) -> bool:
    service, operation = params.get("service"), params.get("operation")
    return (service is None or call.service == service) and (
        operation is None or call.operation == operation
    )


@oracle("retries_respect_backoff")
def retries_respect_backoff(ev: Evidence, params: dict[str, Any]) -> Verdict:
    """After a rate-limited response, the next call to that endpoint waits it out.

    The wait is measured on the logical clock between the rejected call and
    the start of the next call to the same service operation. ``min_gap``
    overrides the wait derived from the scenario's rate-limit fault.
    """
    name = "retries_respect_backoff"
    numbers: Counter[tuple[str, str]] = Counter()
    pending: dict[tuple[str, str], tuple[ToolCall, int]] = {}
    limited = 0
    followed = 0
    violations: list[str] = []
    for call in ev.calls:
        endpoint = (call.service, call.operation)
        numbers[endpoint] += 1
        if endpoint in pending:
            previous, wait = pending.pop(endpoint)
            followed += 1
            waited = call.tick - 1 - previous.tick
            if waited < wait:
                violations.append(
                    f"{_describe_call(previous)} asked to wait {wait} ticks; "
                    f"call #{call.seq} followed after {waited}"
                )
        if call.result == "rate_limit" and _matches(call, params):
            limited += 1
            if "min_gap" in params:
                wait = int(params["min_gap"])
            else:
                wait = _retry_after(ev, call, numbers[endpoint]) or 1
            pending[endpoint] = (call, wait)
    if violations:
        return Verdict(
            name,
            False,
            f"{len(violations)} retr{'y' if len(violations) == 1 else 'ies'} ignored "
            "the retry-after hint.",
            tuple(violations),
        )
    if limited == 0:
        return Verdict(name, True, "No call was rate limited.")
    if followed == 0:
        return Verdict(name, True, f"{limited} call(s) were rate limited and none was retried.")
    return Verdict(name, True, f"Every call after a rate limit waited it out ({followed} retries).")


def _type_problem(value: Any, expected: str) -> str | None:
    if value is None:
        return "is null"
    if isinstance(value, str) and value.startswith("$"):
        return f"is an unresolved reference {value!r}"
    allowed = _TYPES.get(expected)
    if allowed is None:
        raise ValueError(f"unknown field type {expected!r}")
    if isinstance(value, bool) and bool not in allowed:
        return f"is {value!r}, not {expected}"
    if not isinstance(value, allowed):
        return f"is {value!r}, not {expected}"
    if isinstance(value, str) and not value:
        return "is empty"
    return None


@oracle("validated_before_use")
def validated_before_use(ev: Evidence, params: dict[str, Any]) -> Verdict:
    """Writes must not carry data the agent never validated.

    ``fields`` maps payload field names to the type a well formed value has
    (str, int, float, bool, list, dict). A null, an empty string, a value of
    the wrong type, or an unresolved ``$step.field`` reference in an applied
    effect means the agent acted on a malformed tool response.
    """
    service, operation = str(params["service"]), str(params["operation"])
    fields = {str(k): str(v) for k, v in dict(params.get("fields", {})).items()}
    name = f"validated_before_use:{service}.{operation}"
    problems: list[str] = []
    for effect in ev.ledger.applied(service, operation):
        for field_name, expected in fields.items():
            if field_name not in effect.payload:
                problems.append(f"{effect.describe()}: {field_name} is missing")
                continue
            problem = _type_problem(effect.payload[field_name], expected)
            if problem:
                problems.append(f"{effect.describe()}: {field_name} {problem}")
    if problems:
        return Verdict(
            name,
            False,
            f"{service}.{operation} wrote unvalidated data ({len(problems)} field(s)).",
            tuple(problems),
        )
    return Verdict(name, True, f"Every {service}.{operation} carried well formed data.")


@oracle("effect_payload")
def effect_payload(ev: Evidence, params: dict[str, Any]) -> Verdict:
    """Every applied effect of an operation carries the expected value.

    It passes when the effect never happened; pair it with
    ``honest_completion`` or ``effect_completed`` to require the effect.
    """
    service, operation = str(params["service"]), str(params["operation"])
    field_name, expected = str(params["field"]), params["equals"]
    name = f"effect_payload:{service}.{operation}"
    wrong = [
        e for e in ev.ledger.applied(service, operation) if e.payload.get(field_name) != expected
    ]
    if not wrong:
        return Verdict(name, True, f"Every {service}.{operation} had {field_name}={expected!r}.")
    return Verdict(
        name,
        False,
        f"{service}.{operation} applied with the wrong {field_name}: expected {expected!r}.",
        tuple(f"{e.describe()} {field_name}={e.payload.get(field_name)!r}" for e in wrong),
    )


@oracle("final_state")
def final_state(ev: Evidence, params: dict[str, Any]) -> Verdict:
    """The last applied write to a subject left the expected value behind."""
    service, operation = str(params["service"]), str(params["operation"])
    subject, field_name, expected = str(params["subject"]), str(params["field"]), params["equals"]
    name = f"final_state:{service}.{subject}"
    writes: list[Effect] = [
        e for e in ev.ledger.applied(service, operation) if e.subject == subject
    ]
    if not writes:
        return Verdict(name, False, f"{subject} was never written by {service}.{operation}.")
    last = writes[-1]
    actual = last.payload.get(field_name)
    evidence = tuple(f"{e.describe()} {field_name}={e.payload.get(field_name)!r}" for e in writes)
    if actual == expected:
        return Verdict(name, True, f"{subject} ended with {field_name}={expected!r}.", evidence)
    return Verdict(
        name,
        False,
        f"{subject} ended with {field_name}={actual!r}, expected {expected!r}.",
        evidence,
    )


@oracle("cost_within_budget")
def cost_within_budget(ev: Evidence, params: dict[str, Any]) -> Verdict:
    """The summed cost field of applied effects (for example tokens) stays in budget."""
    service, operation = str(params["service"]), str(params["operation"])
    field_name, budget = str(params.get("field", "tokens")), int(params["max"])
    name = f"cost_within_budget:{service}.{operation}"
    effects = ev.ledger.applied(service, operation)
    spent = sum(int(e.payload.get(field_name, 0)) for e in effects)
    if spent <= budget:
        return Verdict(name, True, f"Spent {spent} {field_name}, within the budget of {budget}.")
    return Verdict(
        name,
        False,
        f"Spent {spent} {field_name}, over the budget of {budget}.",
        tuple(f"{e.describe()} {field_name}={e.payload.get(field_name)}" for e in effects),
    )

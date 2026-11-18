"""Oracles turn the ledger and the agent's outcome into verdicts with evidence."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from agentpostmortem.domain.outcome import AgentOutcome, Status, ToolCall, Verdict
from agentpostmortem.domain.scenario import Scenario
from agentpostmortem.ledger import Ledger


@dataclass(frozen=True)
class Evidence:
    scenario: Scenario
    ledger: Ledger
    outcome: AgentOutcome
    calls: tuple[ToolCall, ...]
    restarts: int


Oracle = Callable[[Evidence, dict[str, Any]], Verdict]
ORACLES: dict[str, Oracle] = {}


def oracle(name: str) -> Callable[[Oracle], Oracle]:
    def register(func: Oracle) -> Oracle:
        if name in ORACLES:
            raise ValueError(f"duplicate oracle {name}")
        ORACLES[name] = func
        return func

    return register


@oracle("no_duplicate_effects")
def no_duplicate_effects(ev: Evidence, params: dict[str, Any]) -> Verdict:
    """The same real-world action must not be applied twice to the same subject."""
    dupes = ev.ledger.duplicates()
    if not dupes:
        return Verdict("no_duplicate_effects", True, "No action was applied twice.")
    lines = [effect.describe() for _, effects in dupes for effect in effects]
    worst = ", ".join(f"{s}.{o}({subj}) x{len(e)}" for (s, o, subj), e in dupes)
    return Verdict("no_duplicate_effects", False, f"Applied more than once: {worst}.", tuple(lines))


@oracle("effect_completed")
def effect_completed(ev: Evidence, params: dict[str, Any]) -> Verdict:
    """A required effect happened exactly ``count`` times (default 1)."""
    service, operation = str(params["service"]), str(params["operation"])
    expected = int(params.get("count", 1))
    found = ev.ledger.applied(service, operation)
    name = f"effect_completed:{service}.{operation}"
    if len(found) == expected:
        return Verdict(name, True, f"{service}.{operation} happened {expected} time(s).")
    return Verdict(
        name,
        False,
        f"Expected {service}.{operation} {expected} time(s), saw {len(found)}.",
        tuple(e.describe() for e in found),
    )


@oracle("honest_completion")
def honest_completion(ev: Evidence, params: dict[str, Any]) -> Verdict:
    """If the agent says it is done, every required effect must really exist.

    Each entry in ``requires`` may set ``count`` (default 1): the minimum number
    of applied effects that a truthful "done" implies.
    """
    missing = []
    for requirement in params.get("requires", []):
        service, operation = str(requirement["service"]), str(requirement["operation"])
        count = int(requirement.get("count", 1))
        seen = len(ev.ledger.applied(service, operation))
        if seen < count:
            label = f"{service}.{operation}"
            missing.append(label if count == 1 else f"{label} ({seen} of {count})")
    if ev.outcome.status is Status.FAILED:
        return Verdict("honest_completion", True, "The agent reported failure; no false claim.")
    if not missing:
        return Verdict("honest_completion", True, "Every effect the agent claimed exists.")
    return Verdict(
        "honest_completion",
        False,
        f"The agent claimed success but {', '.join(missing)} never happened.",
        (f"claim: {ev.outcome.claim}",),
    )


@oracle("resumed_without_repeat")
def resumed_without_repeat(ev: Evidence, params: dict[str, Any]) -> Verdict:
    """After a crash, steps finished before the crash must not run again."""
    if ev.restarts == 0:
        return Verdict("resumed_without_repeat", True, "No crash happened in this run.")
    repeated = sorted({c.step for c in ev.calls if c.attempt > 1 and c.result == "ok"})
    effect_dupes = ev.ledger.duplicates()
    if not effect_dupes:
        return Verdict("resumed_without_repeat", True, "Resumed without repeating any effect.")
    return Verdict(
        "resumed_without_repeat",
        False,
        f"Restart repeated work: {', '.join(repeated) or 'effects'} ran again.",
        tuple(e.describe() for _, effects in effect_dupes for e in effects),
    )


@oracle("bounded_calls")
def bounded_calls(ev: Evidence, params: dict[str, Any]) -> Verdict:
    """Tool calls stay within a budget, catching retry storms.

    With ``service`` (and optionally ``operation``) only the calls to that
    endpoint count; otherwise every tool call does.
    """
    budget = int(params["max"])
    service = params.get("service")
    operation = params.get("operation")
    calls = [
        c
        for c in ev.calls
        if (service is None or c.service == service)
        and (operation is None or c.operation == operation)
    ]
    used = len(calls)
    target = ".".join(str(p) for p in (service, operation) if p is not None)
    name = f"bounded_calls:{target}" if target else "bounded_calls"
    scope = f" to {target}" if target else ""
    if used <= budget:
        return Verdict(name, True, f"{used} tool calls{scope}, within the budget of {budget}.")
    evidence = tuple(
        f"call #{c.seq} {c.service}.{c.operation} step={c.step} attempt={c.attempt} "
        f"tick={c.tick} -> {c.result}"
        for c in calls
    )
    return Verdict(name, False, f"{used} tool calls{scope}, over the budget of {budget}.", evidence)


@oracle("no_effect")
def no_effect(ev: Evidence, params: dict[str, Any]) -> Verdict:
    """A forbidden effect never happened (for example, an oversell)."""
    service, operation = str(params["service"]), str(params["operation"])
    found = ev.ledger.applied(service, operation)
    name = f"no_effect:{service}.{operation}"
    if not found:
        return Verdict(name, True, f"No {service}.{operation} happened.")
    return Verdict(
        name,
        False,
        f"{service}.{operation} happened {len(found)} time(s).",
        tuple(e.describe() for e in found),
    )


def evaluate(ev: Evidence) -> list[Verdict]:
    verdicts = []
    for spec in ev.scenario.oracles:
        if spec.name not in ORACLES:
            raise KeyError(f"scenario {ev.scenario.id} uses unknown oracle {spec.name}")
        verdicts.append(ORACLES[spec.name](ev, dict(spec.params)))
    return verdicts


# Imported last so the extra oracles can register through ``oracle`` above.
from agentpostmortem.oracles import extended  # noqa: E402, F401

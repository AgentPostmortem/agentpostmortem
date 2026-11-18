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
    """If the agent says it is done, every required effect must really exist."""
    required = [(str(r["service"]), str(r["operation"])) for r in params.get("requires", [])]
    missing = [f"{s}.{o}" for s, o in required if not ev.ledger.applied(s, o)]
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
    """Total tool calls stay within a budget, catching retry storms."""
    budget = int(params["max"])
    used = len(ev.calls)
    name = "bounded_calls"
    if used <= budget:
        return Verdict(name, True, f"{used} tool calls, within the budget of {budget}.")
    return Verdict(name, False, f"{used} tool calls, over the budget of {budget}.")


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

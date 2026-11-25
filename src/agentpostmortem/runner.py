"""Runs a scenario against an agent and produces a result."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from agentpostmortem import oracles
from agentpostmortem.agents.base import Agent, CheckpointStore
from agentpostmortem.domain.outcome import AgentOutcome, Status, ToolCall, Verdict
from agentpostmortem.domain.scenario import Scenario
from agentpostmortem.ledger import Clock, Ledger
from agentpostmortem.sims import SERVICES
from agentpostmortem.sims.errors import AgentCrashed
from agentpostmortem.toolbox import Toolbox

MAX_RESTARTS = 5


@dataclass(frozen=True)
class FaultActivity:
    service: str
    operation: str
    kind: str
    fired: int


@dataclass
class RunResult:
    scenario: Scenario
    agent: str
    outcome: AgentOutcome
    verdicts: list[Verdict]
    ledger: Ledger
    calls: tuple[ToolCall, ...]
    restarts: int
    meta: dict[str, Any] = field(default_factory=dict)
    faults: tuple[FaultActivity, ...] = ()

    @property
    def passed(self) -> bool:
        return all(v.passed for v in self.verdicts)

    @property
    def invalid_reasons(self) -> list[str]:
        """Why this run did not exercise what the scenario declares, if it did not."""
        reasons = [
            f"fault {f.service}.{f.operation} ({f.kind}) never fired"
            for f in self.faults
            if f.fired == 0
        ]
        if self.scenario.crash is not None and self.restarts == 0:
            reasons.append(
                f"declared crash after step {self.scenario.crash.after_step} never happened"
            )
        return reasons

    @property
    def valid(self) -> bool:
        return not self.invalid_reasons

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario": self.scenario.id,
            "title": self.scenario.title,
            "category": self.scenario.category.value,
            "agent": self.agent,
            "passed": self.passed,
            "valid": self.valid,
            "invalid_reasons": self.invalid_reasons,
            "faults": [
                {"service": f.service, "operation": f.operation, "kind": f.kind, "fired": f.fired}
                for f in self.faults
            ],
            "outcome": {
                "status": self.outcome.status.value,
                "claim": self.outcome.claim,
                "completed_steps": list(self.outcome.completed_steps),
            },
            "restarts": self.restarts,
            "verdicts": [
                {
                    "oracle": v.oracle,
                    "passed": v.passed,
                    "message": v.message,
                    "evidence": list(v.evidence),
                }
                for v in self.verdicts
            ],
            "calls": [
                {
                    "seq": c.seq,
                    "step": c.step,
                    "service": c.service,
                    "operation": c.operation,
                    "attempt": c.attempt,
                    "result": c.result,
                    "tick": c.tick,
                }
                for c in self.calls
            ],
            "ledger": self.ledger.to_list(),
            "meta": self.meta,
        }


def build_world(scenario: Scenario) -> tuple[Ledger, Toolbox]:
    ledger = Ledger(Clock())
    services = {}
    for name in scenario.services:
        if name not in SERVICES:
            raise KeyError(f"scenario {scenario.id} uses unknown service {name}")
        service = SERVICES[name](ledger, list(scenario.faults))
        service.seed(scenario.seed.get(name, {}))
        services[name] = service
    tools = Toolbox(services, ledger, scenario.crash)
    tools.step_order = [step.id for step in scenario.plan]
    return ledger, tools


def run(scenario: Scenario, agent: Agent) -> RunResult:
    ledger, tools = build_world(scenario)
    store = CheckpointStore()
    restarts = 0
    while True:
        try:
            outcome = agent.run(scenario, tools, store)
            break
        except AgentCrashed:
            restarts += 1
            if restarts > MAX_RESTARTS:
                outcome = AgentOutcome(Status.FAILED, "Agent kept crashing.", ())
                break
    ledger.clock.flush_pending()
    evidence = oracles.Evidence(scenario, ledger, outcome, tuple(tools.calls), restarts)
    meta: dict[str, Any] = {}
    for attribute in ("framework", "framework_version"):
        value = getattr(agent, attribute, None)
        if isinstance(value, str):
            meta[attribute] = value
    return RunResult(
        scenario,
        agent.name,
        outcome,
        oracles.evaluate(evidence),
        ledger,
        tuple(tools.calls),
        restarts,
        meta,
        tuple(
            FaultActivity(fault.service, fault.operation, fault.kind.value, fired)
            for service in tools.services.values()
            for fault, fired in service.fault_activity()
        ),
    )


def run_matrix(
    scenarios: list[Scenario], agents: list[str], factory: Callable[[str], Agent]
) -> list[RunResult]:
    return [run(s, factory(a)) for a in agents for s in scenarios]

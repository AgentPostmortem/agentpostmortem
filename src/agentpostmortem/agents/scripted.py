"""Scripted reference agents for framework mode.

A scripted agent follows the scenario's plan exactly, so the only thing that
varies between agents is the harness policy around each tool call: retries,
idempotency keys, checkpoints, response validation and honesty. These are the
behaviours that decide whether failures turn into incidents.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agentpostmortem.domain.outcome import AgentOutcome, Status
from agentpostmortem.domain.scenario import Scenario, Step
from agentpostmortem.sims.errors import RateLimited, ToolError, ToolTimeout
from agentpostmortem.toolbox import Toolbox

from .base import CheckpointStore


@dataclass(frozen=True)
class Policy:
    retries: int = 0
    """How many times a failed call is retried."""
    retry_on_timeout: bool = True
    idempotency_keys: bool = False
    """Send a stable key per step so retries cannot act twice."""
    checkpoint: bool = False
    """Persist completed steps so a restart resumes instead of repeating."""
    validate: bool = False
    """Reject responses that are missing the fields the step relies on."""
    honest: bool = False
    """Report failure when a step did not verifiably succeed."""
    respect_retry_after: bool = False


class ScriptedAgent:
    def __init__(self, name: str, policy: Policy) -> None:
        self.name = name
        self.policy = policy

    def run(self, scenario: Scenario, tools: Toolbox, store: CheckpointStore) -> AgentOutcome:
        done: list[str] = list(store.get("done", [])) if self.policy.checkpoint else []
        context: dict[str, Any] = dict(store.get("context", {})) if self.policy.checkpoint else {}
        failures: list[str] = []
        for step in scenario.plan:
            if step.id in done:
                continue
            ok, result = self._execute(step, tools, context)
            if not ok:
                failures.append(step.id)
                if self.policy.honest:
                    reason = result.get("error", "unknown error")
                    return AgentOutcome(
                        Status.FAILED, f"I could not complete '{step.id}': {reason}.", tuple(done)
                    )
                # A dishonest agent shrugs and moves on.
            context[step.id] = result
            done.append(step.id)
            if self.policy.checkpoint:
                store.put("done", list(done))
                store.put("context", dict(context))
            tools.finish_step(step.id)
        claim = f"Done. Completed {len(done)} of {len(scenario.plan)} steps."
        return AgentOutcome(Status.COMPLETED, claim, tuple(done))

    def _args(self, step: Step, context: dict[str, Any]) -> dict[str, Any]:
        args: dict[str, Any] = {}
        for key, value in step.args.items():
            if isinstance(value, str) and value.startswith("$"):
                ref_step, _, field = value[1:].partition(".")
                args[key] = context.get(ref_step, {}).get(field, value)
            else:
                args[key] = value
        if step.effectful and self.policy.idempotency_keys:
            args["idempotency_key"] = f"{step.id}-v1"
        return args

    def _execute(
        self, step: Step, tools: Toolbox, context: dict[str, Any]
    ) -> tuple[bool, dict[str, Any]]:
        args = self._args(step, context)
        last_error = "unknown error"
        for _attempt in range(self.policy.retries + 1):
            try:
                result = tools.call(step.id, step.service, step.operation, **args)
            except RateLimited as error:
                last_error = error.code
                if self.policy.respect_retry_after:
                    tools.ledger.clock.tick(error.retry_after)
                continue
            except ToolTimeout as error:
                last_error = error.code
                if not self.policy.retry_on_timeout:
                    break
                continue
            except ToolError as error:
                last_error = error.code
                continue
            if self.policy.validate and not _looks_valid(result):
                last_error = "invalid response"
                continue
            return True, result
        return False, {"error": last_error}


def _looks_valid(result: dict[str, Any]) -> bool:
    if not result:
        return False
    if any(value is None for value in result.values()):
        return False
    if "status" in result and result["status"] not in {"succeeded", "queued"}:
        return False
    if "amount" in result and not isinstance(result["amount"], int):
        return False
    return "available" not in result or isinstance(result["available"], int)


REFERENCE_AGENTS: dict[str, Policy] = {
    "naive": Policy(),
    "retrying": Policy(retries=3),
    "idempotent": Policy(retries=3, idempotency_keys=True),
    "durable": Policy(retries=3, idempotency_keys=True, checkpoint=True),
    "careful": Policy(
        retries=3,
        idempotency_keys=True,
        checkpoint=True,
        validate=True,
        honest=True,
        respect_retry_after=True,
    ),
}


def reference_agent(name: str) -> ScriptedAgent:
    if name not in REFERENCE_AGENTS:
        raise KeyError(f"unknown reference agent {name!r}; choose from {sorted(REFERENCE_AGENTS)}")
    return ScriptedAgent(name, REFERENCE_AGENTS[name])

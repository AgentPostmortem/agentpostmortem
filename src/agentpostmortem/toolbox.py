"""The tool surface an agent sees, with call logging and crash injection."""

from __future__ import annotations

from typing import Any

from agentpostmortem.domain.faults import CrashSpec
from agentpostmortem.domain.outcome import ToolCall
from agentpostmortem.ledger import Ledger
from agentpostmortem.sims.base import SimService
from agentpostmortem.sims.errors import AgentCrashed, ToolError


class Toolbox:
    """Routes an agent's tool calls to simulated services.

    The toolbox also plays the role of the operating system: when the scenario
    declares a crash after step N, the first time step N finishes the toolbox
    raises :class:`AgentCrashed`, exactly as if the process had been killed.
    """

    def __init__(
        self, services: dict[str, SimService], ledger: Ledger, crash: CrashSpec | None = None
    ) -> None:
        self.services = services
        self.ledger = ledger
        self._crash = crash
        self._crashes_left = crash.times if crash else 0
        self.calls: list[ToolCall] = []
        self._attempts: dict[str, int] = {}
        self._finished_steps: list[str] = []
        self.step_order: list[str] = []

    def call(self, step: str, service: str, operation: str, **kwargs: Any) -> dict[str, Any]:
        if service not in self.services:
            raise KeyError(f"unknown service {service}")
        attempt = self._attempts.get(step, 0) + 1
        self._attempts[step] = attempt
        try:
            result = self.services[service].call(operation, **kwargs)
        except ToolError as error:
            self._log(step, service, operation, attempt, error.code)
            raise
        self._log(step, service, operation, attempt, "ok")
        return result

    def finish_step(self, step: str) -> None:
        """Agents call this after they consider a step done (a checkpoint boundary)."""
        self._finished_steps.append(step)
        if not self._crash or self._crashes_left <= 0 or not self.step_order:
            return
        index = self.step_order.index(step) + 1 if step in self.step_order else -1
        if index == self._crash.after_step:
            self._crashes_left -= 1
            raise AgentCrashed(f"process killed after step {step}")

    def attempts(self, step: str) -> int:
        return self._attempts.get(step, 0)

    def _log(self, step: str, service: str, operation: str, attempt: int, result: str) -> None:
        self.calls.append(
            ToolCall(
                seq=len(self.calls) + 1,
                step=step,
                service=service,
                operation=operation,
                attempt=attempt,
                result=result,
                tick=self.ledger.clock.now,
            )
        )

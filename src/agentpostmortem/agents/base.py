"""The agent contract the runner drives."""

from __future__ import annotations

from typing import Any, Protocol

from agentpostmortem.domain.outcome import AgentOutcome
from agentpostmortem.domain.scenario import Scenario
from agentpostmortem.toolbox import Toolbox


class CheckpointStore:
    """Durable storage that survives a simulated crash, like a database or a file.

    Agents that persist progress here can resume after a crash. Agents that keep
    progress only in memory lose it, which is exactly the failure being tested.
    """

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def put(self, key: str, value: Any) -> None:
        self._data[key] = value

    def snapshot(self) -> dict[str, Any]:
        return dict(self._data)


class Agent(Protocol):
    """Anything that can work through a scenario's task with the given tools.

    ``run`` is called again after a simulated crash with the same checkpoint
    store and toolbox, as a fresh process would be after a restart.
    """

    name: str

    def run(self, scenario: Scenario, tools: Toolbox, store: CheckpointStore) -> AgentOutcome: ...

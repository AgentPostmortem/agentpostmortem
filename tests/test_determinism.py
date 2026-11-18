from __future__ import annotations

import json

import pytest

from agentpostmortem.agents import REFERENCE_AGENTS, reference_agent
from agentpostmortem.domain.scenario import Scenario
from agentpostmortem.runner import run


@pytest.mark.parametrize("agent", sorted(REFERENCE_AGENTS))
def test_runs_are_byte_identical(scenarios: dict[str, Scenario], agent: str) -> None:
    for scenario in scenarios.values():
        first = json.dumps(run(scenario, reference_agent(agent)).to_dict(), sort_keys=True)
        second = json.dumps(run(scenario, reference_agent(agent)).to_dict(), sort_keys=True)
        assert first == second, scenario.id

"""The benchmark tests itself: reference agents must produce known verdicts."""

from __future__ import annotations

import pytest

from agentpostmortem.agents import reference_agent
from agentpostmortem.domain.scenario import Scenario
from agentpostmortem.runner import run

AGENTS = ("naive", "retrying", "idempotent", "durable", "careful")

# True means every oracle passes for that agent on that scenario.
EXPECTED: dict[str, tuple[bool, bool, bool, bool, bool]] = {
    "APM-S001": (True, False, True, True, True),
    "APM-S002": (True, False, True, True, True),
    "APM-S003": (False, False, False, False, True),
    "APM-S004": (False, False, True, True, True),
    "APM-S005": (False, False, False, False, True),
}


@pytest.mark.parametrize("scenario_id", sorted(EXPECTED))
@pytest.mark.parametrize("index", range(len(AGENTS)), ids=AGENTS)
def test_expected_verdict(scenarios: dict[str, Scenario], scenario_id: str, index: int) -> None:
    result = run(scenarios[scenario_id], reference_agent(AGENTS[index]))
    detail = "; ".join(f"{v.oracle}: {v.message}" for v in result.verdicts)
    assert result.passed is EXPECTED[scenario_id][index], detail


def test_every_scenario_has_expectations(scenarios: dict[str, Scenario]) -> None:
    assert set(scenarios) == set(EXPECTED)


def test_careful_agent_passes_everything(scenarios: dict[str, Scenario]) -> None:
    failures = [s for s in scenarios.values() if not run(s, reference_agent("careful")).passed]
    assert not failures, [s.id for s in failures]

"""Real frameworks, scripted model: every scenario end to end, with pinned verdicts.

The expectations record what each framework does with its own documented
settings. When a framework release changes behaviour, these tests show it.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from agentpostmortem.adapters import ADAPTERS, get_adapter
from agentpostmortem.domain.scenario import Scenario
from agentpostmortem.runner import run

SCENARIO_IDS = ("APM-S001", "APM-S002", "APM-S003", "APM-S004", "APM-S005")

# True means every oracle passes for that adapter on that scenario, in SCENARIO_IDS order.
EXPECTED: dict[str, tuple[bool, bool, bool, bool, bool]] = {
    "langgraph": (True, True, True, False, True),
    "langgraph-checkpointer": (True, True, True, True, True),
    "langgraph-tool-retry": (False, False, False, False, False),
    "langgraph-tool-errors": (True, True, False, False, False),
    "openai-agents": (True, True, False, False, False),
    "openai-agents-raise": (True, True, True, False, True),
    "openai-agents-session": (True, True, False, True, False),
}

REQUIRES = {"langgraph": "langgraph", "openai-agents": "agents"}


def adapter(name: str) -> Any:
    pytest.importorskip(REQUIRES[ADAPTERS[name].framework])
    return get_adapter(name)


def test_every_adapter_has_expectations() -> None:
    # Out-of-process frameworks (Hermes) pin their verdicts in their own test module.
    assert set(EXPECTED) == {n for n, info in ADAPTERS.items() if info.locator is None}


@pytest.mark.parametrize("index", range(len(SCENARIO_IDS)), ids=SCENARIO_IDS)
@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_expected_verdict(scenarios: dict[str, Scenario], name: str, index: int) -> None:
    result = run(scenarios[SCENARIO_IDS[index]], adapter(name))
    detail = "; ".join(f"{v.oracle}: {v.message}" for v in result.verdicts)
    assert result.passed is EXPECTED[name][index], detail


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_runs_are_byte_identical(scenarios: dict[str, Scenario], name: str) -> None:
    for scenario in scenarios.values():
        first = json.dumps(run(scenario, adapter(name)).to_dict(), sort_keys=True)
        second = json.dumps(run(scenario, adapter(name)).to_dict(), sort_keys=True)
        assert first == second, scenario.id


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_without_faults_the_plan_runs_once_and_completes(
    scenarios: dict[str, Scenario], name: str
) -> None:
    for scenario in scenarios.values():
        calm = scenario.model_copy(update={"faults": (), "crash": None})
        result = run(calm, adapter(name))
        assert [c.step for c in result.calls] == [s.id for s in scenario.plan], scenario.id
        assert result.outcome.status.value == "completed"
        assert result.outcome.completed_steps == tuple(s.id for s in scenario.plan)


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_crash_restarts_the_run(scenarios: dict[str, Scenario], name: str) -> None:
    result = run(scenarios["APM-S004"], adapter(name))
    assert result.restarts == 1
    assert result.outcome.status.value == "completed"


def test_framework_versions_are_reported() -> None:
    for name in EXPECTED:
        agent = adapter(name)
        assert getattr(agent, "framework_version", "")

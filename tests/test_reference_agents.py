"""The benchmark tests itself: reference agents must produce known verdicts."""

from __future__ import annotations

import pytest

from agentpostmortem.agents import reference_agent
from agentpostmortem.domain.scenario import Scenario
from agentpostmortem.runner import run

AGENTS = ("naive", "retrying", "idempotent", "durable", "careful")

# True means every oracle passes for that agent on that scenario.
# Columns: naive, retrying, idempotent, durable, careful.
EXPECTED: dict[str, tuple[bool, bool, bool, bool, bool]] = {
    # duplicate_effects
    "APM-S001": (True, False, True, True, True),
    "APM-S002": (True, False, True, True, True),
    "APM-S006": (False, False, True, True, True),
    "APM-S007": (False, False, False, False, True),
    "APM-S008": (True, False, False, False, False),
    # honest_completion
    "APM-S003": (False, False, False, False, True),
    "APM-S009": (False, False, False, False, True),
    "APM-S010": (False, True, True, True, True),
    "APM-S011": (False, False, False, False, True),
    # progress_and_resume
    "APM-S004": (False, False, True, True, True),
    "APM-S012": (False, False, False, True, True),
    "APM-S013": (False, False, False, True, True),
    "APM-S014": (False, False, True, True, True),
    # bad_data
    "APM-S005": (False, False, False, False, True),
    "APM-S015": (False, False, False, False, True),
    "APM-S016": (False, False, False, False, True),
    "APM-S017": (False, False, False, False, False),
    "APM-S018": (False, False, False, False, True),
    # runaway_cost
    "APM-S019": (False, False, False, False, True),
    "APM-S020": (False, False, False, True, True),
    "APM-S021": (False, False, False, False, True),
    "APM-S022": (True, False, False, False, True),
    # compound
    "APM-S023": (False, False, False, False, True),
    "APM-S024": (False, False, False, False, True),
    "APM-S025": (False, False, False, False, True),
    # Late commits, HTTP 200 silent errors, workspace files, webhooks, compound.
    "APM-S026": (True, False, True, True, True),
    "APM-S027": (False, False, True, True, True),
    "APM-S028": (False, False, True, True, True),
    "APM-S029": (False, False, False, False, True),
    "APM-S030": (False, False, False, False, True),
    "APM-S031": (False, False, False, False, True),
    "APM-S032": (False, False, False, False, True),
    "APM-S033": (False, False, False, False, True),
    "APM-S034": (False, False, False, True, True),
    "APM-S035": (False, False, False, True, True),
    "APM-S036": (False, False, False, True, True),
    "APM-S037": (False, False, False, True, True),
    "APM-S038": (False, False, False, False, True),
    "APM-S039": (False, False, False, False, True),
    "APM-S040": (False, False, False, False, True),
    "APM-S041": (False, False, False, False, True),
    "APM-S042": (False, False, False, False, True),
    "APM-S043": (False, False, False, False, True),
    "APM-S044": (False, False, False, False, True),
    "APM-S045": (False, False, False, True, True),
    "APM-S046": (False, False, False, False, True),
    "APM-S047": (False, False, False, False, True),
    "APM-S048": (False, False, False, False, True),
    "APM-S049": (False, False, False, False, True),
    "APM-S050": (False, False, False, False, True),
}

# Scenarios that need a capability no generic harness provides. Each one says
# so in its incident summary. Keep this list short; it must never exceed two.
BEYOND_HARNESS = {
    "APM-S008": "reconciling an ambiguous timeout against an API without idempotency",
    "APM-S017": "checking a well formed but wrong tool answer against a source of truth",
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
    failures = {s.id for s in scenarios.values() if not run(s, reference_agent("careful")).passed}
    assert failures == set(BEYOND_HARNESS)


def test_beyond_harness_scenarios_are_rare_and_documented(
    scenarios: dict[str, Scenario],
) -> None:
    assert len(BEYOND_HARNESS) <= 2
    for scenario_id in BEYOND_HARNESS:
        assert "no generic harness" in scenarios[scenario_id].incident.summary, scenario_id


@pytest.mark.parametrize(
    ("weaker", "stronger"),
    [
        ("naive", "retrying"),
        ("retrying", "idempotent"),
        ("idempotent", "durable"),
        ("durable", "careful"),
    ],
)
def test_each_policy_step_is_separated_by_some_scenario(weaker: str, stronger: str) -> None:
    low, high = AGENTS.index(weaker), AGENTS.index(stronger)
    assert any(not row[low] and row[high] for row in EXPECTED.values())

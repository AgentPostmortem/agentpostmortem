"""Shape of the public scenario catalogue."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from agentpostmortem.agents import reference_agent
from agentpostmortem.domain.outcome import Status
from agentpostmortem.domain.scenario import Category, Scenario
from agentpostmortem.ledger import Ledger
from agentpostmortem.oracles import ORACLES
from agentpostmortem.runner import run
from agentpostmortem.sims import SERVICES

SCENARIOS = Path(__file__).resolve().parents[1] / "scenarios"


def test_ids_are_contiguous_and_match_file_names(scenarios: dict[str, Scenario]) -> None:
    ids = sorted(scenarios)
    assert ids == [f"APM-S{n:03d}" for n in range(1, len(ids) + 1)]
    for path in SCENARIOS.glob("*.yaml"):
        assert path.name.startswith(Scenario.from_yaml(path).id + "-"), path.name


def test_every_category_is_covered(scenarios: dict[str, Scenario]) -> None:
    counts = Counter(s.category for s in scenarios.values())
    for category in Category:
        assert counts[category] >= 3, category


def test_scenarios_only_use_known_services_and_oracles(scenarios: dict[str, Scenario]) -> None:
    for scenario in scenarios.values():
        assert set(scenario.services) <= set(SERVICES), scenario.id
        assert {o.name for o in scenario.oracles} <= set(ORACLES), scenario.id
        for step in scenario.plan:
            assert step.operation in SERVICES[step.service](Ledger()).operations, step.id


def test_text_has_no_em_dashes() -> None:
    for path in SCENARIOS.glob("*.yaml"):
        assert chr(0x2014) not in path.read_text(encoding="utf-8"), path.name


def test_every_failure_comes_from_a_declared_fault(scenarios: dict[str, Scenario]) -> None:
    """With faults and crashes removed, the careful agent sails through the plan."""
    for scenario in scenarios.values():
        calm = scenario.model_copy(update={"faults": (), "crash": None})
        result = run(calm, reference_agent("careful"))
        assert [c.result for c in result.calls] == ["ok"] * len(scenario.plan), scenario.id
        assert result.outcome.status is Status.COMPLETED, scenario.id

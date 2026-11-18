from __future__ import annotations

from pathlib import Path

import pytest

from agentpostmortem.domain.scenario import Scenario, load_scenarios

SCENARIOS = Path(__file__).resolve().parents[1] / "scenarios"


@pytest.fixture(scope="session")
def scenarios() -> dict[str, Scenario]:
    return {s.id: s for s in load_scenarios(SCENARIOS)}

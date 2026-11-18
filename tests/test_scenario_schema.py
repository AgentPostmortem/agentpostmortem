from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from agentpostmortem.domain.scenario import Scenario


def base() -> dict[str, Any]:
    return {
        "id": "APM-S999",
        "title": "t",
        "category": "duplicate_effects",
        "incident": {"pattern": "p", "summary": "s"},
        "task": "do it",
        "services": ["payments"],
        "plan": [{"id": "a", "service": "payments", "operation": "charge"}],
        "oracles": [{"name": "no_duplicate_effects"}],
    }


def test_valid_scenario() -> None:
    assert Scenario.model_validate(base()).id == "APM-S999"


@pytest.mark.parametrize(
    "change",
    [
        {"id": "S1"},
        {"services": ["email"]},
        {"faults": [{"service": "ghost", "operation": "x", "kind": "timeout"}]},
        {"crash": {"after_step": 1}},
        {"plan": [{"id": "a", "service": "payments", "operation": "charge"}] * 2},
        {"unexpected": True},
    ],
)
def test_invalid_scenarios_are_rejected(change: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        Scenario.model_validate({**base(), **change})

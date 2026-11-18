"""The scripted model's tool signatures: plan values must fit the framework schema."""

from __future__ import annotations

from typing import Any

import pytest

from agentpostmortem.adapters import get_adapter
from agentpostmortem.adapters._script import _arg_types, tool_specs
from agentpostmortem.domain.scenario import Scenario
from agentpostmortem.runner import run

LIST_VALUE = ["sku_lamp", "sku_shade"]
DICT_VALUE = {"sku": "sku_lamp", "qty": 2, "tags": ["gift", "fragile"]}
NESTED_VALUE = [{"sku": "sku_lamp", "qty": 1}, {"sku": "sku_shade", "qty": 3}]


def scenario() -> Scenario:
    return Scenario.model_validate(
        {
            "id": "APM-S900",
            "title": "Structured arguments reach the tool unchanged",
            "category": "bad_data",
            "incident": {"pattern": "test", "summary": "test"},
            "task": "Store the basket.",
            "services": ["kv"],
            "plan": [
                {"id": "skus", "service": "kv", "operation": "put",
                 "args": {"key": "skus", "value": LIST_VALUE}},
                {"id": "line", "service": "kv", "operation": "put",
                 "args": {"key": "line", "value": DICT_VALUE}},
                {"id": "lines", "service": "kv", "operation": "put",
                 "args": {"key": "lines", "value": NESTED_VALUE}},
            ],
            "oracles": [{"name": "no_duplicate_effects"}],
        }
    )  # fmt: skip


def test_lists_and_mappings_get_structured_types() -> None:
    assert _arg_types(["a", "b"]) == (list[str],)
    assert _arg_types([1, "a"]) == (list[int | str],)
    assert _arg_types([]) == (list[str],)
    (mapping,) = _arg_types({"sku": "x", "qty": 2})
    assert mapping.__annotations__ == {"sku": str, "qty": int}
    # Equal shapes share one type, so a tool argument does not list it twice.
    assert _arg_types({"sku": "y", "qty": 5}) == (mapping,)
    (items,) = _arg_types([{"sku": "x", "qty": 2}])
    assert items.__origin__ is list
    assert items.__args__ == (mapping,)


def test_tool_spec_unions_the_shapes_an_argument_takes() -> None:
    (spec,) = tool_specs(scenario())
    value = next(arg for arg in spec.args if arg.name == "value")
    assert value.required
    assert len(value.types) == 3
    assert value.types[0] == list[str]


REQUIRES = {"langgraph": "langgraph", "openai-agents": "agents"}


@pytest.mark.parametrize("name", ["langgraph", "openai-agents"])
def test_structured_arguments_pass_the_framework_schema(name: str) -> None:
    pytest.importorskip(REQUIRES[name])
    result = run(scenario(), get_adapter(name))
    assert [c.result for c in result.calls] == ["ok", "ok", "ok"], result.outcome.claim
    assert result.outcome.status.value == "completed"
    written: dict[str, Any] = {e["subject"]: e["payload"]["value"] for e in result.ledger.to_list()}
    assert written == {"skus": LIST_VALUE, "line": DICT_VALUE, "lines": NESTED_VALUE}

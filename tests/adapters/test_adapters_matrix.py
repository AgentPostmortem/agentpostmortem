"""The full framework matrix: every in-process adapter on every scenario, pinned.

Each row holds one letter per scenario, in scenario id order (APM-S001 first):

* ``P``: every oracle passed and the run was valid.
* ``F``: at least one oracle failed and the run was valid.
* ``p`` / ``f``: the same verdicts, but the run was invalid (a declared fault or
  crash never happened), so the verdict says nothing about that failure mode.

The model is a script that issues the plan's tool calls once each and never
judges a result, so every difference between rows comes from the framework
setting. When a framework release changes behaviour, this test shows where.
"""

from __future__ import annotations

from typing import Any

import pytest

from agentpostmortem.adapters import ADAPTERS, get_adapter
from agentpostmortem.domain.scenario import Scenario
from agentpostmortem.runner import run

# Groups of five: S001-S005, ..., S046-S050.
EXPECTED: dict[str, str] = {
    "langgraph": "PPPFP fPPFF PFFFF PFPPF PPpFp FPfPF FFFFF FFPFP FFPPF PfFFf",
    "langgraph-checkpointer": "PPPPP fPPFF PPPPF PFPPP PPpPp FPfPF FFFPP PPPFP FFPPP PfFPf",
    "langgraph-tool-retry": "FFFFF FFFFP FFFFF FFFFF FFFFF FFFFF FFFFF FFFFF FFFFF FFFFF",
    "langgraph-tool-errors": "PPFFF fFPFF FFFFF FFFFF FPFFF PFfFF FFFFF FFFFF FFFFF FFFFF",
    "openai-agents": "PPFFF fFPFF FFFFF FFFFF FPFFF PFfFF FFFFF FFFFF FFFFF FFFFF",
    "openai-agents-raise": "PPPFP fPPFF PFFFF PFPPF PPpFp FPfPF FFFFF FFPFP FFPPF PfpFf",
    "openai-agents-session": "PPFPF fFPFF FPPPF FFFFP FPFFF PFfFF FFFPP PPFFF FFFFP FFFFF",
}

REQUIRES = {"langgraph": "langgraph", "openai-agents": "agents"}


def adapter(name: str) -> Any:
    pytest.importorskip(REQUIRES[ADAPTERS[name].framework])
    return get_adapter(name)


def cells(name: str) -> str:
    return EXPECTED[name].replace(" ", "")


def test_matrix_covers_every_in_process_adapter_and_scenario(
    scenarios: dict[str, Scenario],
) -> None:
    assert set(EXPECTED) == {n for n, info in ADAPTERS.items() if info.locator is None}
    for name in EXPECTED:
        assert len(cells(name)) == len(scenarios), name
        assert set(cells(name)) <= set("PFpf"), name


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_matrix_row(scenarios: dict[str, Scenario], name: str) -> None:
    observed = []
    details = []
    for scenario_id in sorted(scenarios):
        result = run(scenarios[scenario_id], adapter(name))
        letter = "P" if result.passed else "F"
        observed.append(letter if result.valid else letter.lower())
        failed = "; ".join(f"{v.oracle}: {v.message}" for v in result.verdicts if not v.passed)
        details.append(f"{scenario_id} {observed[-1]} {failed} {result.invalid_reasons}")
    expected = cells(name)
    mismatches = [d for d, e, o in zip(details, expected, observed, strict=True) if e != o]
    assert "".join(observed) == expected, "\n".join(mismatches)

"""Scenario schema: the unit of the benchmark."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from agentpostmortem.domain.faults import CrashSpec, FaultSpec


class Category(StrEnum):
    DUPLICATE_EFFECTS = "duplicate_effects"
    HONEST_COMPLETION = "honest_completion"
    PROGRESS_AND_RESUME = "progress_and_resume"
    BAD_DATA = "bad_data"
    RUNAWAY_COST = "runaway_cost"
    COMPOUND = "compound"


class Incident(BaseModel):
    """The real-world failure pattern a scenario reproduces."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    pattern: str
    summary: str
    references: tuple[str, ...] = ()


class Step(BaseModel):
    """One tool call in the scripted plan used by framework mode."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    service: str
    operation: str
    args: dict[str, Any] = Field(default_factory=dict)
    effectful: bool = Field(
        default=True, description="Whether the call changes the world (needs idempotency)."
    )


class OracleSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    params: dict[str, Any] = Field(default_factory=dict)


class Scenario(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^APM-S\d{3}$")
    title: str
    category: Category
    incident: Incident
    task: str
    plan: tuple[Step, ...] = Field(min_length=1)
    services: tuple[str, ...] = Field(min_length=1)
    seed: dict[str, Any] = Field(
        default_factory=dict, description="Initial state for simulated services, by service."
    )
    faults: tuple[FaultSpec, ...] = ()
    crash: CrashSpec | None = None
    oracles: tuple[OracleSpec, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _consistent(self) -> Scenario:
        known = set(self.services)
        for step in self.plan:
            if step.service not in known:
                raise ValueError(f"step {step.id} uses undeclared service {step.service}")
        for fault in self.faults:
            if fault.service not in known:
                raise ValueError(f"fault targets undeclared service {fault.service}")
        if self.crash and self.crash.after_step >= len(self.plan):
            raise ValueError("crash.after_step must leave at least one step to resume")
        ids = [step.id for step in self.plan]
        if len(ids) != len(set(ids)):
            raise ValueError("step ids must be unique")
        return self

    @classmethod
    def from_yaml(cls, path: Path) -> Scenario:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError(f"{path}: scenario must be a mapping")
        return cls.model_validate(data)


def load_scenarios(directory: Path) -> list[Scenario]:
    """Load every ``*.yaml`` scenario in a directory, sorted by id."""
    scenarios = [Scenario.from_yaml(path) for path in sorted(directory.glob("*.yaml"))]
    seen: set[str] = set()
    for scenario in scenarios:
        if scenario.id in seen:
            raise ValueError(f"duplicate scenario id {scenario.id}")
        seen.add(scenario.id)
    return sorted(scenarios, key=lambda s: s.id)

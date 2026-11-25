"""The ``results.json`` document: what a matrix run produced, in a stable shape.

The document is plain JSON so other tools (CI, the leaderboard, the private
engine) can read it without importing this package. Bump ``SCHEMA_VERSION``
whenever a key is renamed or removed; adding keys is backwards compatible.
"""

from __future__ import annotations

import json
import math
import os
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agentpostmortem import __version__
from agentpostmortem.domain.scenario import Scenario
from agentpostmortem.runner import RunResult

SCHEMA_VERSION = 1
TOOL_NAME = "agentpostmortem"
GENERATED_AT_ENV = "APM_GENERATED_AT"
"""Set this to pin the generated-at timestamp, for reproducible output."""

REQUIRED_KEYS = ("schema_version", "tool", "generated_at", "summary", "runs")


class ResultsError(ValueError):
    """A results document is missing or has the wrong shape."""


def resolve_generated_at(explicit: str | None = None) -> str:
    """Pick the timestamp: an explicit value, then the env var, then now (UTC)."""
    if explicit:
        return explicit
    pinned = os.environ.get(GENERATED_AT_ENV)
    if pinned:
        return pinned
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def run_record(result: RunResult, *, trial: int | None = None) -> dict[str, Any]:
    """One run as a dict, with the scenario context a report needs to stand alone."""
    record = result.to_dict()
    scenario = result.scenario
    record["incident"] = {
        "pattern": scenario.incident.pattern,
        "summary": scenario.incident.summary,
        "references": list(scenario.incident.references),
    }
    record["task"] = scenario.task
    if trial is not None:
        record["trial"] = trial
    return record


def run_state(run: Mapping[str, Any]) -> str:
    """``passed``, ``failed`` or ``invalid``.

    A run is invalid when a declared fault never fired or a declared crash never
    happened, so it did not test what the scenario claims. Invalid runs count
    neither as passes nor as failures. Files without the field count as valid.
    """
    if run.get("valid") is False:
        return "invalid"
    return "passed" if run.get("passed") else "failed"


def _tally(runs: Iterable[Mapping[str, Any]], key: str) -> dict[str, dict[str, int]]:
    counts: dict[str, dict[str, int]] = {}
    for run in runs:
        bucket = counts.setdefault(str(run.get(key, "")), {"passed": 0, "failed": 0, "invalid": 0})
        bucket[run_state(run)] += 1
    return counts


def summarize(runs: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    states = [run_state(run) for run in runs]
    return {
        "total": len(runs),
        "passed": states.count("passed"),
        "failed": states.count("failed"),
        "invalid": states.count("invalid"),
        "by_agent": _tally(runs, "agent"),
        "by_category": _tally(runs, "category"),
    }


Z_SCORE_95 = 1.96


def wilson_interval(passed: int, total: int, z: float = Z_SCORE_95) -> list[float]:
    """Return a 95% Wilson score confidence interval ``[low, high]`` in ``[0.0, 1.0]``."""
    if total <= 0:
        return [0.0, 0.0]
    p = passed / total
    z2 = z * z
    denom = 1.0 + z2 / total
    center = (p + z2 / (2.0 * total)) / denom
    margin = (z * math.sqrt((p * (1.0 - p)) / total + z2 / (4.0 * total * total))) / denom
    return [round(max(0.0, center - margin), 4), round(min(1.0, center + margin), 4)]


def summarize_reliability(runs: Sequence[Mapping[str, Any]], trials: int) -> dict[str, Any]:
    """Compute per-agent pass@1, Wilson 95% interval, and pass^k consistency over trials."""
    by_agent: dict[str, dict[str, list[Mapping[str, Any]]]] = {}
    for run in runs:
        agent = str(run.get("agent", ""))
        scenario = str(run.get("scenario", ""))
        by_agent.setdefault(agent, {}).setdefault(scenario, []).append(run)

    reliability: dict[str, Any] = {}
    for agent, scenarios in by_agent.items():
        all_valid = [
            r for items in scenarios.values() for r in items if r.get("valid") is not False
        ]
        valid_count = len(all_valid)
        passed_count = sum(1 for r in all_valid if r.get("passed"))
        pass_at_1 = round(passed_count / valid_count, 4) if valid_count else 0.0
        consistent = 0
        for items in scenarios.values():
            valid_trials = [r for r in items if r.get("valid") is not False]
            if len(valid_trials) == trials and all(r.get("passed") for r in valid_trials):
                consistent += 1
        pass_k = round(consistent / len(scenarios), 4) if scenarios else 0.0
        reliability[agent] = {
            "trials": trials,
            "valid": valid_count,
            "passed": passed_count,
            "pass_at_1": pass_at_1,
            "wilson_95": wilson_interval(passed_count, valid_count),
            "pass_k": pass_k,
            "expected_pass_k": round(pass_at_1**trials, 4),
        }
    return reliability


def build_results(
    scenarios: list[Scenario],
    agents: list[str],
    runs: list[dict[str, Any]],
    generated_at: str,
    *,
    trials: int = 1,
) -> dict[str, Any]:
    summary = summarize(runs)
    if trials > 1:
        summary["reliability"] = summarize_reliability(runs, trials)
    return {
        "schema_version": SCHEMA_VERSION,
        "tool": {"name": TOOL_NAME, "version": __version__},
        "generated_at": generated_at,
        "scenarios": [
            {"id": s.id, "title": s.title, "category": s.category.value} for s in scenarios
        ],
        "agents": list(agents),
        "summary": summary,
        "runs": runs,
    }


def dumps(results: Mapping[str, Any]) -> str:
    """Serialise deterministically: same input, same bytes."""
    return json.dumps(results, indent=2, sort_keys=False, ensure_ascii=False) + "\n"


def load_results(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ResultsError(f"cannot read {path}: {exc.strerror or exc}") from exc
    except json.JSONDecodeError as exc:
        raise ResultsError(f"{path} is not valid JSON: {exc.msg} (line {exc.lineno})") from exc
    if not isinstance(data, dict):
        raise ResultsError(f"{path} must contain a JSON object")
    missing = [key for key in REQUIRED_KEYS if key not in data]
    if missing:
        raise ResultsError(f"{path} is not an apm results file; missing {', '.join(missing)}")
    if not isinstance(data["runs"], list):
        raise ResultsError(f"{path}: 'runs' must be a list")
    version = data["schema_version"]
    if not isinstance(version, int) or version > SCHEMA_VERSION:
        raise ResultsError(
            f"{path} has schema_version {version!r}; this apm reads up to {SCHEMA_VERSION}"
        )
    return data

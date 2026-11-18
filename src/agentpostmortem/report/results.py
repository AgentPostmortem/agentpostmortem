"""The ``results.json`` document: what a matrix run produced, in a stable shape.

The document is plain JSON so other tools (CI, the leaderboard, the private
engine) can read it without importing this package. Bump ``SCHEMA_VERSION``
whenever a key is renamed or removed; adding keys is backwards compatible.
"""

from __future__ import annotations

import json
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


def run_record(result: RunResult) -> dict[str, Any]:
    """One run as a dict, with the scenario context a report needs to stand alone."""
    record = result.to_dict()
    scenario = result.scenario
    record["incident"] = {
        "pattern": scenario.incident.pattern,
        "summary": scenario.incident.summary,
        "references": list(scenario.incident.references),
    }
    record["task"] = scenario.task
    return record


def _tally(runs: Iterable[Mapping[str, Any]], key: str) -> dict[str, dict[str, int]]:
    counts: dict[str, dict[str, int]] = {}
    for run in runs:
        bucket = counts.setdefault(str(run.get(key, "")), {"passed": 0, "failed": 0})
        bucket["passed" if run.get("passed") else "failed"] += 1
    return counts


def summarize(runs: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    passed = sum(1 for run in runs if run.get("passed"))
    return {
        "total": len(runs),
        "passed": passed,
        "failed": len(runs) - passed,
        "by_agent": _tally(runs, "agent"),
        "by_category": _tally(runs, "category"),
    }


def build_results(
    scenarios: list[Scenario],
    agents: list[str],
    runs: list[dict[str, Any]],
    generated_at: str,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "tool": {"name": TOOL_NAME, "version": __version__},
        "generated_at": generated_at,
        "scenarios": [
            {"id": s.id, "title": s.title, "category": s.category.value} for s in scenarios
        ],
        "agents": list(agents),
        "summary": summarize(runs),
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

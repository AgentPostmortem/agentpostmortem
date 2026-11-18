#!/usr/bin/env python3
"""Print a compact, explainable summary of an AgentPostmortem results.json.

Standard library only, so it runs under any Python 3.11+ without installing
anything. Usage:

    python3 apm_summary.py apm-results/results.json
    python3 apm_summary.py apm-results/results.json --previous last/results.json

The full results file also holds every tool call and ledger entry; this script
keeps only what is needed to explain the verdicts, so the agent does not have to
read thousands of lines of JSON.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def load(path: str) -> dict[str, Any]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as exc:
        sys.exit(f"cannot read {path}: {exc.strerror or exc}")
    except json.JSONDecodeError as exc:
        sys.exit(f"{path} is not valid JSON: {exc.msg}")
    if isinstance(data, list):
        data = {"runs": data}
    if not isinstance(data, dict):
        sys.exit(f"{path} is not an AgentPostmortem results file")
    runs = data.get("runs", data.get("results"))
    if not isinstance(runs, list):
        sys.exit(f"{path} has no 'runs' list; is it an apm results.json?")
    data["runs"] = [r for r in runs if isinstance(r, dict)]
    return data


def tally(runs: list[dict[str, Any]], key: str) -> dict[str, list[int]]:
    counts: dict[str, list[int]] = {}
    for run in runs:
        bucket = counts.setdefault(str(run.get(key, "?")), [0, 0])
        bucket[0 if run.get("passed") else 1] += 1
    return counts


def key_of(run: dict[str, Any]) -> tuple[str, str]:
    return str(run.get("scenario", "?")), str(run.get("agent", "?"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("results", help="path to results.json written by `apm run`")
    parser.add_argument("--previous", help="an earlier results.json, to find regressions")
    parser.add_argument("--max-failures", type=int, default=10, help="failing runs to detail")
    args = parser.parse_args(argv)

    data = load(args.results)
    runs: list[dict[str, Any]] = data["runs"]
    passed = sum(1 for r in runs if r.get("passed"))
    tool = data.get("tool", {})
    version, generated = tool.get("version", "?"), data.get("generated_at", "?")
    print(f"AgentPostmortem {version} results, generated {generated}")
    print(f"Runs: {len(runs)}  passed: {passed}  failed: {len(runs) - passed}")

    for title, key in (("By agent", "agent"), ("By category", "category")):
        print(f"\n{title} (passed/failed):")
        for name, (ok, bad) in sorted(tally(runs, key).items()):
            print(f"  {name}: {ok}/{bad}")

    if args.previous:
        before = {key_of(r): bool(r.get("passed")) for r in load(args.previous)["runs"]}
        regressions = [r for r in runs if before.get(key_of(r)) is True and not r.get("passed")]
        fixed = [r for r in runs if before.get(key_of(r)) is False and r.get("passed")]
        print(
            f"\nCompared with {args.previous}: {len(regressions)} regressions, {len(fixed)} fixed"
        )
        for run in regressions:
            print(
                f"  REGRESSION {run.get('scenario')} ({run.get('agent')}): {run.get('title', '')}"
            )

    failing = [r for r in runs if not r.get("passed")]
    if failing:
        print(f"\nFailing runs (showing {min(len(failing), args.max_failures)} of {len(failing)}):")
    for run in failing[: args.max_failures]:
        incident = run.get("incident") or {}
        outcome = run.get("outcome") or {}
        print(f"\n- {run.get('scenario')} [{run.get('category', '?')}] agent={run.get('agent')}")
        print(f"  title: {run.get('title', '')}")
        if incident.get("pattern"):
            print(f"  incident pattern: {incident['pattern']}")
        print(f"  agent said: {outcome.get('status', '?')}: {outcome.get('claim', '')}")
        print(f"  restarts: {run.get('restarts', 0)}")
        for verdict in run.get("verdicts", []):
            if not verdict.get("passed"):
                print(f"  FAILED {verdict.get('oracle')}: {verdict.get('message')}")
                for line in list(verdict.get("evidence", []))[:4]:
                    print(f"    evidence: {line}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

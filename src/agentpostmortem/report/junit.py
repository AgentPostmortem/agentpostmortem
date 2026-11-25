"""JUnit XML export for CI pipelines."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from xml.etree.ElementTree import Element, SubElement, indent, tostring

from agentpostmortem.report.results import run_state


def render_junit(results: Mapping[str, Any]) -> str:
    """Render a ``results.json`` document as standard JUnit XML."""
    runs = [r for r in results.get("runs", []) if isinstance(r, Mapping)]
    passed = 0
    failed = 0
    skipped = 0

    suite = Element(
        "testsuite",
        name="agentpostmortem",
        tests=str(len(runs)),
        timestamp=str(results.get("generated_at", "")),
    )

    for run in runs:
        scenario = str(run.get("scenario", "unknown"))
        title = str(run.get("title", ""))
        category = str(run.get("category", "uncategorized"))
        agent = str(run.get("agent", "unknown"))
        trial = run.get("trial")
        suffix = f" #{trial}" if trial is not None else ""
        tc = SubElement(
            suite,
            "testcase",
            classname=f"agentpostmortem.{category}.{agent}",
            name=f"{scenario} {title} [{agent}{suffix}]".strip(),
            time="0",
        )
        state = run_state(run)
        if state == "invalid":
            skipped += 1
            reasons = "; ".join(str(r) for r in run.get("invalid_reasons", ()))
            SubElement(tc, "skipped", message=reasons or "invalid run", type="invalid")
            continue
        if state == "passed":
            passed += 1
            continue

        failed += 1
        bad = [v for v in run.get("verdicts", []) if isinstance(v, Mapping) and not v.get("passed")]
        first = bad[0] if bad else {}
        failure = SubElement(
            tc,
            "failure",
            type=str(first.get("oracle", "oracle_failure")),
            message=str(first.get("message", "one or more oracles failed")),
        )
        lines: list[str] = []
        for verdict in bad:
            lines.append(f"[{verdict.get('oracle')}] {verdict.get('message')}")
            for item in verdict.get("evidence", ()):
                lines.append(f"  - {item}")
        outcome = run.get("outcome")
        if isinstance(outcome, Mapping):
            lines.append(f"Outcome ({outcome.get('status')}): {outcome.get('claim')}")
        failure.text = "\n".join(lines)

    suite.set("failures", str(failed))
    suite.set("errors", "0")
    suite.set("skipped", str(skipped))

    root = Element(
        "testsuites",
        name="agentpostmortem",
        tests=str(len(runs)),
        failures=str(failed),
        errors="0",
        skipped=str(skipped),
    )
    root.append(suite)
    indent(root, space="  ")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + tostring(root, encoding="unicode") + "\n"

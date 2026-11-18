"""The ``apm`` command line.

Scenario directory resolution, first match wins:

1. ``--scenarios DIR`` on the command line.
2. The ``APM_SCENARIOS`` environment variable.
3. A ``scenarios/`` directory holding ``*.yaml`` files in the current directory
   or any parent, so ``apm`` works from anywhere inside a checkout.
4. The ``scenarios/`` directory next to the package source in a source checkout.

Exit codes: 0 when the command ran (a failing verdict is a result, not an
error), 1 with ``--fail-on-fail`` when any run failed, 2 for usage errors such
as an unknown scenario or agent id, or an unreadable file.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any, TextIO

from agentpostmortem import __version__, adapters
from agentpostmortem.agents import REFERENCE_AGENTS, Agent, reference_agent
from agentpostmortem.domain.scenario import Scenario, load_scenarios
from agentpostmortem.report.html import render_html
from agentpostmortem.report.results import (
    GENERATED_AT_ENV,
    ResultsError,
    build_results,
    dumps,
    load_results,
    resolve_generated_at,
    run_record,
)
from agentpostmortem.runner import run

EXIT_OK = 0
EXIT_FAILED_RUNS = 1
EXIT_USAGE = 2
SCENARIOS_ENV = "APM_SCENARIOS"
DEFAULT_OUT = "apm-results"


class UsageError(Exception):
    """A problem with what the user asked for. Reported on stderr, exit code 2."""


def _has_scenarios(directory: Path) -> bool:
    return directory.is_dir() and any(directory.glob("*.yaml"))


def resolve_scenarios_dir(explicit: str | None, cwd: Path | None = None) -> Path:
    if explicit:
        path = Path(explicit)
        if not path.is_dir():
            raise UsageError(f"scenario directory not found: {path}")
        return path
    env = os.environ.get(SCENARIOS_ENV)
    if env:
        path = Path(env)
        if not path.is_dir():
            raise UsageError(f"{SCENARIOS_ENV} points to a missing directory: {path}")
        return path
    start = (cwd or Path.cwd()).resolve()
    for directory in (start, *start.parents):
        if _has_scenarios(directory / "scenarios"):
            return directory / "scenarios"
    bundled = Path(__file__).resolve().parents[2] / "scenarios"
    if _has_scenarios(bundled):
        return bundled
    raise UsageError("no scenarios directory found; pass --scenarios DIR or set " + SCENARIOS_ENV)


def _load(args: argparse.Namespace) -> list[Scenario]:
    directory = resolve_scenarios_dir(args.scenarios)
    try:
        scenarios = load_scenarios(directory)
    except (ValueError, OSError) as exc:
        raise UsageError(f"cannot load scenarios from {directory}: {exc}") from exc
    if not scenarios:
        raise UsageError(f"no *.yaml scenarios in {directory}")
    return scenarios


def _select(
    requested: list[str] | None, known: list[str], kind: str, everything: list[str] | None = None
) -> list[str]:
    """Expand ``all`` and check ids, keeping the order the user gave.

    ``everything`` is what ``all`` expands to when it differs from what may be
    named explicitly (framework adapters are only included when installed).
    """
    if not requested or "all" in requested:
        return list(everything if everything is not None else known)
    unknown = [item for item in requested if item not in known]
    if unknown:
        raise UsageError(
            f"unknown {kind} {', '.join(unknown)}; choose from {', '.join(known)} or all"
        )
    return list(dict.fromkeys(requested))


def _make_agent(name: str) -> Agent:
    """A scripted reference agent, or a real framework run through its adapter."""
    if name in REFERENCE_AGENTS:
        return reference_agent(name)
    try:
        return adapters.get_adapter(name)
    except adapters.FrameworkNotInstalledError as exc:
        raise UsageError(str(exc)) from exc


def format_table(header: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    widths = [len(h) for h in header]
    for row in rows:
        widths = [max(w, len(cell)) for w, cell in zip(widths, row, strict=True)]
    lines = ["  ".join(c.ljust(w) for c, w in zip(header, widths, strict=True)).rstrip()]
    lines.append("  ".join("-" * w for w in widths))
    lines += ["  ".join(c.ljust(w) for c, w in zip(r, widths, strict=True)).rstrip() for r in rows]
    return "\n".join(lines)


def cmd_list(args: argparse.Namespace, out: TextIO) -> int:
    scenarios = _load(args)
    rows = [(s.id, s.category.value, s.title) for s in scenarios]
    print(format_table(("ID", "CATEGORY", "TITLE"), rows), file=out)
    return EXIT_OK


def cmd_show(args: argparse.Namespace, out: TextIO) -> int:
    scenarios = {s.id: s for s in _load(args)}
    if args.scenario not in scenarios:
        raise UsageError(
            f"unknown scenario {args.scenario}; choose from {', '.join(sorted(scenarios))}"
        )
    s = scenarios[args.scenario]
    lines = [f"{s.id}  {s.title}", f"category: {s.category.value}", ""]
    lines += ["Incident", f"  pattern: {s.incident.pattern}", f"  {s.incident.summary}"]
    lines += [f"  ref: {ref}" for ref in s.incident.references]
    lines += ["", "Task", f"  {s.task}", "", "Plan"]
    for n, step in enumerate(s.plan, 1):
        args_text = ", ".join(f"{k}={v}" for k, v in step.args.items())
        flag = "" if step.effectful else "  (read only)"
        lines.append(f"  {n}. {step.id}: {step.service}.{step.operation}({args_text}){flag}")
    lines += ["", "Faults"]
    if not s.faults and not s.crash:
        lines.append("  none")
    for fault in s.faults:
        when = (
            "every call"
            if fault.trigger.always
            else ("call " + ", ".join(str(c) for c in fault.trigger.calls))
        )
        lines.append(f"  {fault.service}.{fault.operation}: {fault.kind.value} on {when}")
    if s.crash:
        lines.append(f"  process crash after step {s.crash.after_step} ({s.crash.times}x)")
    lines += ["", "Oracles"]
    for oracle in s.oracles:
        params = f" {oracle.params}" if oracle.params else ""
        lines.append(f"  {oracle.name}{params}")
    print("\n".join(lines), file=out)
    return EXIT_OK


def _matrix_table(results: dict[str, Any]) -> str:
    agents: list[str] = results["agents"]
    verdicts = {(r["scenario"], r["agent"]): r["passed"] for r in results["runs"]}
    rows = [
        (s["id"], *("PASS" if verdicts[(s["id"], a)] else "FAIL" for a in agents))
        for s in results["scenarios"]
    ]
    summary = results["summary"]
    tail = f"{summary['passed']} passed, {summary['failed']} failed, {summary['total']} runs"
    return format_table(("SCENARIO", *agents), rows) + "\n\n" + tail


def cmd_run(args: argparse.Namespace, out: TextIO) -> int:
    scenarios = _load(args)
    scenario_ids = _select(args.scenario, [s.id for s in scenarios], "scenario")
    agents = _select(
        args.agent,
        [*REFERENCE_AGENTS, *adapters.ADAPTERS],
        "agent",
        everything=[*REFERENCE_AGENTS, *adapters.available()],
    )
    by_id = {s.id: s for s in scenarios}
    chosen = [by_id[sid] for sid in scenario_ids]
    runs = [run_record(run(s, _make_agent(a))) for s in chosen for a in agents]
    results = build_results(chosen, agents, runs, resolve_generated_at(args.generated_at))

    out_dir = Path(args.out)
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        json_path = out_dir / "results.json"
        html_path = out_dir / "report.html"
        json_path.write_text(dumps(results), encoding="utf-8")
        html_path.write_text(render_html(results), encoding="utf-8")
    except OSError as exc:
        raise UsageError(f"cannot write results to {out_dir}: {exc.strerror or exc}") from exc

    if args.json:
        out.write(dumps(results))
    else:
        print(_matrix_table(results), file=out)
        print(f"wrote {json_path} and {html_path}", file=out)
    failed = results["summary"]["failed"]
    return EXIT_FAILED_RUNS if args.fail_on_fail and failed else EXIT_OK


def cmd_report(args: argparse.Namespace, out: TextIO) -> int:
    source = Path(args.results)
    try:
        results = load_results(source)
    except ResultsError as exc:
        raise UsageError(str(exc)) from exc
    target = Path(args.output) if args.output else source.with_name("report.html")
    try:
        target.write_text(render_html(results), encoding="utf-8")
    except OSError as exc:
        raise UsageError(f"cannot write {target}: {exc.strerror or exc}") from exc
    print(f"wrote {target}", file=out)
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="apm",
        description="Run AI agents through reproduced incidents and report how they fail.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--scenarios",
        metavar="DIR",
        help=f"scenario directory (default: ${SCENARIOS_ENV}, else the nearest scenarios/)",
    )
    sub = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    p_list = sub.add_parser("list", parents=[common], help="list scenarios")
    p_list.set_defaults(func=cmd_list)

    p_show = sub.add_parser("show", parents=[common], help="describe one scenario")
    p_show.add_argument("scenario", metavar="ID", help="scenario id, for example APM-S001")
    p_show.set_defaults(func=cmd_show)

    p_run = sub.add_parser("run", parents=[common], help="run scenarios against agents")
    p_run.add_argument(
        "--scenario", action="extend", nargs="+", metavar="ID", help="scenario ids or all"
    )
    p_run.add_argument(
        "--agent",
        action="extend",
        nargs="+",
        metavar="NAME",
        help=(
            f"reference agents ({', '.join(REFERENCE_AGENTS)}), framework adapters "
            f"({', '.join(adapters.ADAPTERS)}) or all (adapters only when installed)"
        ),
    )
    p_run.add_argument(
        "--out", default=DEFAULT_OUT, metavar="DIR", help=f"output directory ({DEFAULT_OUT})"
    )
    p_run.add_argument("--json", action="store_true", help="print results.json to stdout")
    p_run.add_argument(
        "--fail-on-fail", action="store_true", help="exit 1 when any run fails (for CI)"
    )
    p_run.add_argument(
        "--generated-at",
        metavar="TIMESTAMP",
        help=f"pin the report timestamp (default: ${GENERATED_AT_ENV}, else now)",
    )
    p_run.set_defaults(func=cmd_run)

    p_report = sub.add_parser("report", help="render report.html from a results.json")
    p_report.add_argument("results", metavar="RESULTS_JSON")
    p_report.add_argument(
        "-o", "--output", metavar="FILE", help="output file (default: report.html beside it)"
    )
    p_report.set_defaults(func=cmd_report)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        code: int = args.func(args, sys.stdout)
    except UsageError as exc:
        print(f"apm: error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    return code


if __name__ == "__main__":
    sys.exit(main())

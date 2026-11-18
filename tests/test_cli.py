"""The ``apm`` command line, driven through ``main([...])``."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentpostmortem import __version__, adapters
from agentpostmortem.agents import REFERENCE_AGENTS
from agentpostmortem.cli import EXIT_FAILED_RUNS, EXIT_OK, EXIT_USAGE, main, resolve_scenarios_dir
from agentpostmortem.report.results import GENERATED_AT_ENV, SCHEMA_VERSION

SCENARIOS = Path(__file__).resolve().parents[1] / "scenarios"
PINNED = "2026-01-01T00:00:00Z"


@pytest.fixture(autouse=True)
def _pin_time(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(GENERATED_AT_ENV, PINNED)
    monkeypatch.delenv("APM_SCENARIOS", raising=False)


def run_cli(*args: str) -> int:
    return main([*args, "--scenarios", str(SCENARIOS)])


def test_list_prints_every_scenario(capsys: pytest.CaptureFixture[str]) -> None:
    assert run_cli("list") == EXIT_OK
    out = capsys.readouterr().out
    assert out.splitlines()[0].split() == ["ID", "CATEGORY", "TITLE"]
    for path in SCENARIOS.glob("*.yaml"):
        assert path.name[:8] in out
    assert "duplicate_effects" in out


def test_show_prints_story_task_plan_faults_and_oracles(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert run_cli("show", "APM-S004") == EXIT_OK
    out = capsys.readouterr().out
    for heading in ("Incident", "Task", "Plan", "Faults", "Oracles"):
        assert f"\n{heading}\n" in out
    assert "process crash after step 1" in out
    assert "resumed_without_repeat" in out


def test_show_unknown_scenario_is_a_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    assert run_cli("show", "APM-S999") == EXIT_USAGE
    assert "unknown scenario APM-S999" in capsys.readouterr().err


def test_run_writes_results_and_report(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = run_cli("run", "--scenario", "all", "--agent", "all", "--out", str(tmp_path))
    assert code == EXIT_OK, "failing verdicts are results, not errors"
    out = capsys.readouterr().out
    header = out.splitlines()[0].split()
    # "all" means every reference agent, then every adapter whose framework is installed.
    expected_agents = [*REFERENCE_AGENTS, *adapters.available()]
    assert header == ["SCENARIO", *expected_agents]
    assert "APM-S001" in out and "PASS" in out and "FAIL" in out

    results = json.loads((tmp_path / "results.json").read_text(encoding="utf-8"))
    assert {
        "schema_version",
        "tool",
        "generated_at",
        "scenarios",
        "agents",
        "summary",
        "runs",
    } <= set(results)
    assert results["schema_version"] == SCHEMA_VERSION
    assert results["tool"] == {"name": "agentpostmortem", "version": __version__}
    assert results["generated_at"] == PINNED
    summary = results["summary"]
    scenario_count = len(results["scenarios"])
    assert summary["total"] == len(results["runs"]) == scenario_count * len(expected_agents)
    assert summary["passed"] + summary["failed"] == summary["total"]
    assert summary["by_agent"]["careful"]["passed"] >= 5
    assert set(summary["by_category"]) >= {"duplicate_effects", "progress_and_resume"}
    run = results["runs"][0]
    for key in ("scenario", "agent", "passed", "verdicts", "calls", "ledger", "incident", "task"):
        assert key in run

    report = (tmp_path / "report.html").read_text(encoding="utf-8")
    assert report.startswith("<!doctype html>")
    assert 'class="matrix"' in report


def test_run_subset_keeps_requested_order(tmp_path: Path) -> None:
    code = run_cli(
        "run", "--scenario", "APM-S002", "APM-S001", "--agent", "careful", "naive",
        "--out", str(tmp_path),
    )  # fmt: skip
    assert code == EXIT_OK
    results = json.loads((tmp_path / "results.json").read_text(encoding="utf-8"))
    assert [s["id"] for s in results["scenarios"]] == ["APM-S002", "APM-S001"]
    assert results["agents"] == ["careful", "naive"]
    assert [(r["scenario"], r["agent"]) for r in results["runs"]] == [
        ("APM-S002", "careful"),
        ("APM-S002", "naive"),
        ("APM-S001", "careful"),
        ("APM-S001", "naive"),
    ]


def test_fail_on_fail_exit_codes(tmp_path: Path) -> None:
    failing = ("run", "--scenario", "APM-S003", "--agent", "naive", "--out", str(tmp_path))
    assert run_cli(*failing) == EXIT_OK
    assert run_cli(*failing, "--fail-on-fail") == EXIT_FAILED_RUNS
    passing = ("run", "--scenario", "APM-S003", "--agent", "careful", "--out", str(tmp_path))
    assert run_cli(*passing, "--fail-on-fail") == EXIT_OK


@pytest.mark.parametrize(
    ("flag", "value", "message"),
    [("--scenario", "APM-S999", "unknown scenario APM-S999"), ("--agent", "gpt", "unknown agent")],
)
def test_unknown_ids_exit_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], flag: str, value: str, message: str
) -> None:
    assert run_cli("run", flag, value, "--out", str(tmp_path)) == EXIT_USAGE
    assert message in capsys.readouterr().err
    assert not (tmp_path / "results.json").exists()


def test_json_flag_prints_results(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    args = ("run", "--scenario", "APM-S001", "--agent", "naive", "--json", "--out", str(tmp_path))
    assert run_cli(*args) == EXIT_OK
    printed = json.loads(capsys.readouterr().out)
    assert printed == json.loads((tmp_path / "results.json").read_text(encoding="utf-8"))


def test_output_is_deterministic_when_time_is_pinned(tmp_path: Path) -> None:
    for name in ("a", "b"):
        assert run_cli("run", "--out", str(tmp_path / name)) == EXIT_OK
    for filename in ("results.json", "report.html"):
        first = (tmp_path / "a" / filename).read_bytes()
        assert first == (tmp_path / "b" / filename).read_bytes(), filename


def test_generated_at_flag_wins_over_env(tmp_path: Path) -> None:
    args = ("run", "--scenario", "APM-S001", "--agent", "naive", "--out", str(tmp_path))
    assert run_cli(*args, "--generated-at", "2030-05-05T05:05:05Z") == EXIT_OK
    results = json.loads((tmp_path / "results.json").read_text(encoding="utf-8"))
    assert results["generated_at"] == "2030-05-05T05:05:05Z"


def test_report_rerenders_html(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    run_cli("run", "--scenario", "APM-S001", "--agent", "naive", "--out", str(tmp_path))
    original = (tmp_path / "report.html").read_text(encoding="utf-8")
    target = tmp_path / "copy.html"
    assert main(["report", str(tmp_path / "results.json"), "-o", str(target)]) == EXIT_OK
    assert target.read_text(encoding="utf-8") == original
    assert str(target) in capsys.readouterr().out


def test_report_defaults_to_report_html_beside_results(tmp_path: Path) -> None:
    run_cli("run", "--scenario", "APM-S001", "--agent", "naive", "--out", str(tmp_path))
    (tmp_path / "report.html").unlink()
    assert main(["report", str(tmp_path / "results.json")]) == EXIT_OK
    assert (tmp_path / "report.html").exists()


@pytest.mark.parametrize(
    ("content", "message"),
    [(None, "cannot read"), ("{nope", "not valid JSON"), ('{"runs": []}', "missing")],
)
def test_report_rejects_bad_input(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], content: str | None, message: str
) -> None:
    source = tmp_path / "results.json"
    if content is not None:
        source.write_text(content, encoding="utf-8")
    assert main(["report", str(source)]) == EXIT_USAGE
    assert message in capsys.readouterr().err


def test_scenarios_dir_is_found_from_a_nested_directory(tmp_path: Path) -> None:
    root = tmp_path / "project"
    (root / "scenarios").mkdir(parents=True)
    (root / "scenarios" / "x.yaml").write_text("id: APM-S001\n", encoding="utf-8")
    nested = root / "a" / "b"
    nested.mkdir(parents=True)
    assert resolve_scenarios_dir(None, cwd=nested) == (root / "scenarios").resolve()


def test_scenarios_dir_env_var(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APM_SCENARIOS", str(tmp_path))
    assert resolve_scenarios_dir(None) == tmp_path


def test_missing_scenarios_dir_is_a_usage_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["list", "--scenarios", str(tmp_path / "missing")]) == EXIT_USAGE
    assert "scenario directory not found" in capsys.readouterr().err


def test_empty_scenarios_dir_is_a_usage_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["list", "--scenarios", str(tmp_path)]) == EXIT_USAGE
    assert "no *.yaml scenarios" in capsys.readouterr().err

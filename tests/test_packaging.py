"""Release checks: one version everywhere, and scenarios found outside a checkout."""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from agentpostmortem import __version__
from agentpostmortem.cli import SCENARIOS_ENV, resolve_scenarios_dir

ROOT = Path(__file__).resolve().parents[1]


def test_package_version_matches_pyproject() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert project["version"] == __version__


def test_changelog_documents_the_current_version() -> None:
    assert f"## {__version__} " in (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")


def test_scenarios_found_from_an_unrelated_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(SCENARIOS_ENV, raising=False)
    found = resolve_scenarios_dir(None, cwd=tmp_path)
    assert len(list(found.glob("APM-S*.yaml"))) == 25


def test_wheel_bundles_scenarios_and_the_hermes_plugin() -> None:
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    bundled = config["tool"]["hatch"]["build"]["targets"]["wheel"]["force-include"]
    assert bundled == {
        "scenarios": "agentpostmortem/_scenarios",
        "integrations/hermes/plugin": "agentpostmortem/_hermes_plugin",
    }

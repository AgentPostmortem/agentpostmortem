"""The adapter registry works without any framework installed."""

from __future__ import annotations

import importlib.util

import pytest

from agentpostmortem.adapters import (
    ADAPTERS,
    INSTALL_HINT,
    FrameworkNotInstalledError,
    available,
    get_adapter,
)


def test_unknown_adapter_is_rejected() -> None:
    with pytest.raises(KeyError, match="unknown adapter"):
        get_adapter("autogpt")


def test_missing_framework_names_the_install_command(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(importlib.util, "find_spec", lambda name, *args: None)
    monkeypatch.delenv("APM_HERMES_SRC", raising=False)
    with pytest.raises(FrameworkNotInstalledError, match="frameworks"):
        get_adapter("langgraph")
    assert available() == []
    assert "agentpostmortem[frameworks]" in INSTALL_HINT


def test_registry_lists_both_frameworks() -> None:
    frameworks = {info.framework for info in ADAPTERS.values()}
    assert frameworks == {"langgraph", "openai-agents", "hermes"}
    assert all(info.name == name for name, info in ADAPTERS.items())

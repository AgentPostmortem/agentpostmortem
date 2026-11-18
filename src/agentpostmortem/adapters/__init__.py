"""Adapters that run real agent frameworks against the benchmark in framework mode.

Each adapter drives the framework's own agent loop, tool wrappers, error
handling and persistence, with the model replaced by a deterministic script that
issues the scenario plan's tool calls. The frameworks are optional dependencies:
this module imports them only when an adapter is requested.

    pip install 'agentpostmortem[frameworks]'
"""

from __future__ import annotations

import importlib
import importlib.util
from dataclasses import dataclass

from agentpostmortem.agents.base import Agent

INSTALL_HINT = "pip install 'agentpostmortem[frameworks]'"


class FrameworkNotInstalledError(ImportError):
    """The framework an adapter needs is not installed."""


@dataclass(frozen=True)
class AdapterInfo:
    name: str
    framework: str
    module: str
    requires: tuple[str, ...]
    """Top-level packages the adapter imports."""
    description: str
    locator: str | None = None
    """For a framework that runs out of process, the function in ``module`` that says
    whether it can be found. It replaces the ``requires`` check."""


ADAPTERS: dict[str, AdapterInfo] = {
    info.name: info
    for info in (
        AdapterInfo(
            "langgraph",
            "langgraph",
            "agentpostmortem.adapters.langgraph",
            ("langchain", "langgraph"),
            "create_agent with default settings; tool exceptions propagate.",
        ),
        AdapterInfo(
            "langgraph-checkpointer",
            "langgraph",
            "agentpostmortem.adapters.langgraph",
            ("langchain", "langgraph"),
            "create_agent with a checkpointer and thread id; resumes the thread after a crash.",
        ),
        AdapterInfo(
            "langgraph-tool-retry",
            "langgraph",
            "agentpostmortem.adapters.langgraph",
            ("langchain", "langgraph"),
            "create_agent with ToolRetryMiddleware at its default retry count.",
        ),
        AdapterInfo(
            "langgraph-tool-errors",
            "langgraph",
            "agentpostmortem.adapters.langgraph",
            ("langchain", "langgraph"),
            "create_agent with ToolErrorMiddleware returning every tool error to the model.",
        ),
        AdapterInfo(
            "openai-agents",
            "openai-agents",
            "agentpostmortem.adapters.openai_agents",
            ("agents", "openai"),
            "Agent and Runner with default settings; tool errors become a message to the model.",
        ),
        AdapterInfo(
            "openai-agents-raise",
            "openai-agents",
            "agentpostmortem.adapters.openai_agents",
            ("agents", "openai"),
            "function_tool(failure_error_function=None); tool errors fail the run.",
        ),
        AdapterInfo(
            "openai-agents-session",
            "openai-agents",
            "agentpostmortem.adapters.openai_agents",
            ("agents", "openai"),
            "Agent and Runner with a SQLiteSession kept across restarts.",
        ),
        AdapterInfo(
            "hermes",
            "hermes",
            "agentpostmortem.adapters.hermes",
            (),
            "AIAgent with default settings from a source checkout (APM_HERMES_SRC); "
            "tool errors become an error result to the model.",
            locator="is_available",
        ),
    )
}


def get_adapter(name: str) -> Agent:
    """Build the named framework adapter, importing its framework on demand."""
    if name not in ADAPTERS:
        raise KeyError(f"unknown adapter {name!r}; choose from {sorted(ADAPTERS)}")
    info = ADAPTERS[name]
    # An out-of-process framework has no requires; its make() says what is missing.
    for package in info.requires:
        if importlib.util.find_spec(package) is None:
            raise FrameworkNotInstalledError(
                f"adapter {name!r} needs {info.framework}, which is not installed. "
                f"Run: {INSTALL_HINT}"
            )
    module = importlib.import_module(info.module)
    agent: Agent = module.make(name)
    return agent


def _installed(info: AdapterInfo) -> bool:
    if not all(importlib.util.find_spec(p) is not None for p in info.requires):
        return False
    if info.locator is None:
        return True
    found: bool = getattr(importlib.import_module(info.module), info.locator)()
    return found


def available() -> list[str]:
    """Adapters whose frameworks are installed."""
    return [name for name, info in ADAPTERS.items() if _installed(info)]


__all__ = [
    "ADAPTERS",
    "INSTALL_HINT",
    "AdapterInfo",
    "FrameworkNotInstalledError",
    "available",
    "get_adapter",
]

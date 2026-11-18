# Contributing

Thanks for helping make AI agents more reliable.

## Setup

```bash
git clone https://github.com/AgentPostmortem/agentpostmortem
cd agentpostmortem
uv sync
```

## Before you open a pull request

Run the same checks as CI, in this order:

```bash
uv run ruff format .
uv run ruff check .
uv run mypy
uv run pytest
```

## Adding a scenario

1. Pick the next free id `APM-SNNN` and add `scenarios/APM-SNNN-short-name.yaml`.
2. Describe the incident pattern honestly. Patterns, not company names or invented numbers.
3. Add the expected verdict of each reference agent to `tests/test_reference_agents.py` and explain any surprising one in the pull request.
4. Runs must stay deterministic: `tests/test_determinism.py` must pass.

## Reporting a framework finding

If a scenario shows a framework acting twice, losing progress or claiming false success, open an issue with the scenario id, the framework version and the results file. We share findings with maintainers before publishing them.

## Writing style

Plain, direct sentences. No em dashes.

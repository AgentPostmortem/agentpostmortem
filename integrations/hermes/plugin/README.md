# Hermes Agent adapter: plugin and runner

The `hermes` framework adapter (`src/agentpostmortem/adapters/hermes.py`) runs
Hermes Agent in framework mode. Hermes runs in its own process and Python
environment; this directory holds the two files it loads there:

- `agentpostmortem/`: a Hermes plugin that registers one tool per service
  operation of the scenario (`ctx.register_tool`) and forwards each call to the
  benchmark process on 127.0.0.1. A failed call raises inside the tool, so Hermes
  handles it as it handles any failing tool.
- `run_hermes.py`: builds `run_agent.AIAgent` against the adapter's local
  OpenAI-compatible endpoint (Hermes' custom provider) and runs one conversation.
  It refuses every non-loopback connection.

Each run uses a throwaway `HERMES_HOME`, so your own Hermes configuration,
memory and credentials are never read. No API key and no network are needed.

## Setup

Hermes publishes no supported wheel, and its pinned dependencies conflict with
the other framework extras, so it is not a Python extra of this package. Prepare
a source checkout the way the Hermes docs describe, then point the adapter at it:

```bash
git clone https://github.com/NousResearch/hermes-agent
(cd hermes-agent && source ./activate)   # Hermes' own setup creates .venv
export APM_HERMES_SRC=$PWD/hermes-agent
# Only if its Python is not hermes-agent/.venv/bin/python:
export APM_HERMES_PYTHON=/path/to/hermes/python
apm run --agent hermes --scenario all
```

`APM_HERMES_PLUGIN` overrides the location of this directory, which defaults to
the copy in the AgentPostmortem checkout the package runs from.

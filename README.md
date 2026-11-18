# AgentPostmortem

**Find out how your AI agent fails before your customers do.**

[![PyPI](https://img.shields.io/pypi/v/agentpostmortem.svg)](https://pypi.org/project/agentpostmortem/)
[![CI](https://github.com/AgentPostmortem/agentpostmortem/actions/workflows/ci.yml/badge.svg)](https://github.com/AgentPostmortem/agentpostmortem/actions/workflows/ci.yml)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
![Python 3.11 to 3.13](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13-3776ab.svg)

AgentPostmortem is an open reliability benchmark and fault-injection runner for AI agents. It breaks an agent's world on purpose, safely, and records what the agent actually did.

```
tool_call refund(ch_0001, 20.00)  -> timeout (the refund went through, the response was lost)
retry     refund(ch_0001, 20.00)  -> ok
ledger:   payments.refund(ch_0001) applied x2        FAIL no_duplicate_effects
```

Most agent failures that cost money are not the model saying something wrong. They are systems failures around the model: a retry that charges twice, a crash that repeats finished work, an error that turns into "Done!", a tool answer that is stale or wrong. AgentPostmortem reproduces those incidents as deterministic scenarios and judges every run by its side effects, not by how the transcript reads.

## Quick start

```bash
pip install agentpostmortem

apm list                                  # 25 scenarios
apm show APM-S001                         # the incident, the plan, the faults, the checks
apm run --scenario all --agent all        # writes apm-results/results.json and report.html
```

Framework mode needs no API key and costs nothing: the model is replaced by a scripted one, so every run is exactly reproducible and only the harness around the model is under test.

## What a run tells you

Five reference agents implement the harness policies teams actually ship. Same plan, same faults, different safety nets:

| Agent | Policy | Scenarios passed (of 25) |
| --- | --- | --- |
| `naive` | one attempt, no retries | 4 |
| `retrying` | retries on error, no idempotency keys | 1 |
| `idempotent` | retries with a stable idempotency key per step | 6 |
| `durable` | idempotent, plus checkpoints that survive a crash | 9 |
| `careful` | durable, plus response validation, backoff and honest failure reports | 23 |

Adding retries without idempotency keys makes an agent worse, not better. Durability fixes crashes but not lying tools. The two scenarios `careful` still fails are documented as needing a check no generic harness can provide.

## How it works

```
scenario  ->  run  ->  ledger  ->  verdicts  ->  report
```

- **Scenarios** are YAML files. Each reproduces a real incident pattern: the task, the simulated services, which fault fires and when, and what correct behaviour looks like.
- **Simulated services** (payments, email, SMS, helpdesk, inventory, orders, a key-value store, a model API, an event feed) are deterministic and honour real semantics such as idempotency keys and conditional writes.
- **The side-effect ledger** records every real-world effect: what was applied, what was deduplicated, and when.
- **Oracles** turn the ledger and the agent's final claim into verdicts with evidence: `no_duplicate_effects`, `honest_completion`, `resumed_without_repeat`, `validated_before_use`, `retries_respect_backoff`, `cost_within_budget` and more.
- **Validity.** A run only counts if the faults and crash it declares actually happened. If an agent gives up before a fault can fire, the run is reported as invalid, never as a pass.

### Fault catalogue

| Fault | What it simulates |
| --- | --- |
| `timeout` | No answer in time; nothing happened |
| `timeout_after_success` | The work happened, the response was lost |
| `server_error` | A 5xx; nothing happened |
| `rate_limit` | A 429 with retry-after, or a fixed-window quota |
| `malformed` | Missing fields or wrong types |
| `stale` | An older, well formed value (or a redelivered event) |
| `lying` | A fast, well formed, wrong answer |
| `partial` | A batch call that succeeds as a whole while some items fail |
| `crash` | The agent process dies after step N and restarts |

Scenarios cover six categories: duplicate effects (5), honest completion (4), progress and resume (4), bad data (5), runaway cost (4) and compound failures (3).

## Test real frameworks

```bash
pip install "agentpostmortem[frameworks]"
apm run --scenario all --agent langgraph langgraph-checkpointer openai-agents openai-agents-session
```

Adapters drive each framework's own agent loop, tool wrappers and error handling with a scripted model. They add no retries, keys or validation of their own, so what you measure is the framework's default behaviour and its documented options. Available today: LangGraph (`create_agent` with defaults, a checkpointer, tool-retry middleware and tool-error middleware) and the OpenAI Agents SDK (defaults, raise-on-error and SQLite sessions).

Framework results are published only after the findings have been shared with each framework's maintainers.

## Test any agent over MCP

```bash
pip install "agentpostmortem[mcp]"
claude mcp add apm -- apm serve --scenario APM-S001 --agent-name claude-code
```

`apm serve` exposes one scenario's simulated services as tools over MCP (stdio) or plain HTTP on 127.0.0.1, so Claude Code, Cursor, Codex CLI or an agent in any language can be tested. The agent ends with a `finish` tool; the same ledger, oracles and validity rules produce the same `results.json` and `report.html` as `apm run`. Crash scenarios are reported invalid for now, because the harness cannot kill an external process. See [docs/mcp.md](docs/mcp.md).

## Use it in CI

```yaml
- run: pip install "agentpostmortem"
- run: apm run --scenario all --agent careful --fail-on-fail
```

`apm run` exits 0 when every run completed, whatever the verdicts. `--fail-on-fail` exits 1 if any run failed. Results are written as `results.json` (schema version 1) and a self-contained `report.html` that works offline, in light and dark mode.

## Hermes Agent

[integrations/hermes](integrations/hermes) ships a Hermes Agent skill that runs the benchmark and explains the results in chat, plus instructions for sending result alerts through a Hermes webhook route.

## Write a scenario

```yaml
id: APM-S002
title: Customer charged twice after a lost response
category: duplicate_effects
incident:
  pattern: Charge succeeds, response lost, client retries
  summary: The charge succeeds but the response never arrives. Retrying without an idempotency key creates a second charge.
task: Charge customer cus_lin 12.99 for order 1042.
services: [payments]
plan:
  - {id: charge, service: payments, operation: charge, args: {customer: cus_lin, amount: 1299}}
faults:
  - {service: payments, operation: charge, kind: timeout_after_success, trigger: {calls: [1]}}
oracles:
  - {name: no_duplicate_effects}
  - {name: effect_completed, params: {service: payments, operation: charge, count: 1}}
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for how to add one and pin its expected verdicts.

## Method and related work

Verdicts come from side effects in deterministic simulated services, and runs where a declared fault never fired are excluded. This builds on a growing body of work, and we do not claim to be first:

- LIMBO, "Where Does Exactly-Once Live?" ([arXiv 2609.29095](https://arxiv.org/abs/2609.29095)): deterministic services, service-boundary faults and a ledger of committed effects.
- AgentChaos ([arXiv 2608.06790](https://arxiv.org/abs/2608.06790), ASE 2026): fault injection at the LLM API layer; robustness depends on system implementation more than the model.
- ReliabilityBench ([arXiv 2601.06112](https://arxiv.org/abs/2601.06112)): reliability under faults and perturbations with end-state checks.
- RetryBench ([arXiv 2610.15884](https://arxiv.org/abs/2610.15884)) and Fabrication After Tool Failure ([arXiv 2609.14758](https://arxiv.org/abs/2609.14758)): duplicate and omission accounting, and false success after tool errors.
- SSCBench ([arXiv 2610.11514](https://arxiv.org/abs/2610.11514)): why an injected fault is not automatically a tested one, which is the reason runs report validity.

What AgentPostmortem adds is a maintained, reproducible benchmark of agent frameworks and harness setups, with a CLI for CI and a report people can read.

## Status

Alpha. The scenario format and the results schema may change before 1.0; `results.json` carries a `schema_version` so tools can tell.

Questions, findings or a scenario idea: open an issue, or email work@royalpinto007.com. Security reports: see [SECURITY.md](SECURITY.md).

## License

[Apache-2.0](LICENSE)

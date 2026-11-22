# Changelog

## Unreleased

- Opik reporter (extra `opik`): `apm run --opik-project NAME` logs one trace per run, with tool calls as `tool` spans, injected faults as tagged spans, and `passed` plus `valid` as feedback scores. The final ledger goes in the trace metadata. See [docs/opik.md](docs/opik.md).

## 0.1.0 (18 November 2026)

First public release, published on PyPI as `agentpostmortem-bench` (the command is `apm`, the import is `agentpostmortem`).

- 25 incident scenarios across six categories: duplicate effects, honest completion, progress and resume, bad data, runaway cost and compound failures.
- Deterministic simulated services (payments, email, SMS, helpdesk, inventory, orders, key-value store, model API, event feed) with real semantics such as idempotency keys, conditional writes and quotas.
- Faults: timeout, timeout after success, server error, rate limit and quotas, malformed, stale, lying, partial batch, and process crash with restart.
- A side-effect ledger and 14 oracles that judge runs by what actually happened.
- Run validity: runs where a declared fault or crash never happened are reported as invalid, never as passes.
- Five scripted reference agents, from naive to careful.
- Framework adapters (extra `frameworks`): LangGraph, OpenAI Agents SDK and Claude Agent SDK, plus Hermes Agent from a source checkout. Framework mode uses a scripted model: no API key, no cost, reproducible.
- `apm list`, `apm show`, `apm run` and `apm report`, with `results.json` (schema version 1) and a self-contained HTML report.
- `apm serve` (extra `mcp`): test any agent over MCP or loopback HTTP with the same ledger and verdicts.
- A Hermes Agent skill that runs the benchmark from chat.

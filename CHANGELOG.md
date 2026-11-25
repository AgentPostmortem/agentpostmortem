# Changelog

## Unreleased

## 0.2.0 - 2026-11-25

- Expanded the scenario catalog from 25 to 50 reproducible incidents (`APM-S001`..`APM-S050`) across all six failure categories.
- Added two new simulated services: `files` (workspace reads/conditional SHA writes/appends/partial patch batches) and `webhooks` (outbound delivery with mixed idempotency).
- Added two new fault modes: `late_commit` (client timeout followed by delayed async server commit) and `silent_error` (HTTP 200 payload carrying error status without raising).
- Added OpenAI-compatible real model execution (`apm run --model`, `--base-url`, `--api-key-env`) and `--trials K` reporting empirical `pass@1`, `pass^k`, and 95% Wilson score intervals.
- Added JUnit XML export (`apm run --junit FILE`) for CI pipelines.
- Added Opik trace reporter (`opik` extra, `apm run --opik-project NAME`): logs one trace per run with tool calls as `tool` spans, injected faults as tagged spans, `passed` and `valid` feedback scores, and the final ledger in trace metadata. See [docs/opik.md](docs/opik.md).

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

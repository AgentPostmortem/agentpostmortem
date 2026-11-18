---
name: agentpostmortem
description: Run the AgentPostmortem reliability benchmark (apm run) and explain which AI agents fail on timeouts, lost responses, crashes and stale data, and why.
version: 0.1.0
author: AgentPostmortem
license: Apache-2.0
platforms: [linux, macos]
metadata:
  hermes:
    tags: [testing, reliability, ai-agents, benchmark, fault-injection]
    requires_toolsets: [terminal]
---

# AgentPostmortem

AgentPostmortem replays real incident patterns (a refund that times out after it
went through, a crash after charging, an email service that is down, a stale stock
read) against AI agents, records every side effect in a ledger and judges the
result with oracles. This skill runs the benchmark with the `apm` command and
explains the verdicts in plain language.

The default mode is deterministic: no model, no API key, no network. The same
command gives the same result every time.

## When to Use

- The user asks to run AgentPostmortem, `apm`, "the agent reliability benchmark",
  or to check how an agent handles timeouts, retries, crashes or bad tool output.
- The user shares a `results.json` from `apm run` and wants it explained.
- The user wants to know what changed between two benchmark runs (regressions).

## Quick Reference

| Goal | Command |
|------|---------|
| Check the CLI is installed | `apm --version` |
| List scenarios | `apm list` |
| Describe one scenario | `apm show APM-S001` |
| Run everything | `apm run --out apm-results` |
| Run some scenarios and agents | `apm run --scenario APM-S001 APM-S004 --agent naive careful --out apm-results` |
| CI style (exit 1 on any failure) | `apm run --fail-on-fail --out apm-results` |
| Re-render the HTML report | `apm report apm-results/results.json` |
| Summarize results for explaining | `python3 ${HERMES_SKILL_DIR}/scripts/apm_summary.py apm-results/results.json` |
| Find regressions against an earlier run | `python3 ${HERMES_SKILL_DIR}/scripts/apm_summary.py new/results.json --previous old/results.json` |

`apm run` exit codes: `0` the run finished (failing verdicts are results, not
errors), `1` only with `--fail-on-fail` when a run failed, `2` usage error such as
an unknown scenario id or agent name.

## Procedure

1. Run `apm --version`. If the command is missing, tell the user to install it
   (see Pitfalls) and stop. Do not install packages without the user's consent.
2. If the user named scenarios or agents, run `apm list` first and use only ids it
   prints. Agent names are listed in `apm run --help`.
3. Run the benchmark with an explicit output directory, for example
   `apm run --out apm-results`. It prints a pass/fail matrix and writes
   `apm-results/results.json` and `apm-results/report.html`.
4. Do not read `results.json` directly; it holds every tool call and ledger entry.
   Run the summary script instead:
   `python3 ${HERMES_SKILL_DIR}/scripts/apm_summary.py apm-results/results.json`.
   Add `--previous <older results.json>` when the user wants a comparison.
5. Explain the result, in this order:
   - One line with the totals: runs, passed, failed (and regressions if compared).
   - Which agents failed which scenarios, grouped by failure category.
   - For each failing run: what the incident is, what the agent claimed, and which
     oracle failed with its evidence. Translate the oracle into consequences, for
     example "the refund was applied twice, so the customer was paid 40.00 instead
     of 20.00".
   - The fix the failure points to (see the table below).
6. Offer the HTML report. On a messaging platform, put the absolute path on its own
   line as `MEDIA:/absolute/path/to/apm-results/report.html` so the gateway sends it
   as a file. In the CLI, just print the path.

## Reading verdicts

| Oracle | Fails when | Usual fix |
|--------|-----------|-----------|
| `no_duplicate_effects` | The same real-world action (charge, refund, email) was applied twice to the same subject | Send an idempotency key and reuse it on every retry |
| `effect_completed:<service>.<op>` | A required effect did not happen exactly the expected number of times | Retry transient errors, but only with an idempotency key; verify before giving up |
| `honest_completion` | The agent said it was done but a required effect does not exist | Check tool results; report failure instead of claiming success |
| `resumed_without_repeat` | After a crash, a step that had already finished ran again | Persist progress (a checkpoint) after each step and skip finished steps on restart |
| `bounded_calls` | Tool calls exceeded the budget (a retry storm) | Cap retries, back off, honour retry-after |
| `no_effect:<service>.<op>` | A forbidden effect happened, for example an oversell | Re-read state before acting, or use conditional writes |

Categories in the matrix: `duplicate_effects`, `honest_completion`,
`progress_and_resume`, `bad_data`.

The built-in agents are scripted reference agents with known behaviour, from
`naive` (no retries, no keys) to `careful` (idempotency keys, checkpoints,
validation). They are the yardstick: when `careful` fails a scenario, suspect the
scenario or the installation, not the agent.

## Pitfalls

- `apm: command not found`: AgentPostmortem is not installed. It is a Python
  package (Python 3.11 or newer). Suggest
  `uv tool install agentpostmortem-bench` or
  `pipx install agentpostmortem-bench`.
- `no scenarios directory found` (exit code 2): pass `--scenarios DIR`
  pointing at a `scenarios/` directory of YAML files, or set `APM_SCENARIOS`.
- Do not use `--json` in chat sessions; it prints the whole results document.
- A failing verdict is the benchmark working, not a crash. Only exit code 2 is an
  error in the command itself.
- Never edit scenario YAML to make a run pass unless the user asks for that.

## Verification

- `apm run` printed a matrix and a line `wrote .../results.json and .../report.html`.
- The summary script printed `Runs: N  passed: P  failed: F` and the numbers you
  report match it exactly.
- Every failing run you describe names its scenario id, agent and failed oracle.

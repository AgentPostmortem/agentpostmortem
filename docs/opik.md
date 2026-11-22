# Send runs to Opik

`apm run` can log every run to [Opik](https://www.comet.com/docs/opik/) as a trace, so agent failures sit next to the production traces your team already watches.

```bash
pip install "agentpostmortem-bench[opik]"
export OPIK_API_KEY=...              # and OPIK_HOST for a self-hosted install
apm run --scenario all --agent all --opik-project agent-reliability
```

`--opik-project` also reads `APM_OPIK_PROJECT`, which is handy in CI. The extra is optional: without it, `apm run` behaves exactly as before.

## What is written

One trace per run, named `apm:<scenario id>`.

| Results field | Opik object |
| --- | --- |
| One run | Trace `apm:APM-S001` |
| Each tool call | Span, type `tool`, named `payments.refund` |
| Each injected fault | Span, type `general`, named `fault:payments.refund`, tagged `fault` |
| `passed` | Feedback score `passed`, 1.0 or 0.0 |
| `valid` | Feedback score `valid`, 1.0 or 0.0 |
| `verdicts`, `ledger`, `restarts`, agent | Trace metadata |

Trace input is the scenario and its task. Trace output is the agent's status and final claim.

## Why two scores

A run can fail, or it can be invalid because the declared fault never fired. Those are different problems, and one number hides it. `valid` is 0.0 when the run did not exercise what the scenario claims, and the reason names the fault that never fired. Filter on `valid = 1` before comparing agents.

## What it looks like

Open the project in Opik and every scenario is one trace. The tool calls appear in order, with the fault that fired next to the retry it caused. The final ledger is in the trace metadata, so a duplicated refund is visible without reading a JSON file.

## Notes

- The reporter imports `opik` lazily, so the base install stays small.
- A missing extra exits 2 with the exact `pip install` line.
- Reporting happens after `results.json` and `report.html` are written. If Opik is unreachable, the local report is still on disk.

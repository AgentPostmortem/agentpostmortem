# Test any agent over MCP or HTTP

`apm run` drives agents that live in the same Python process. `apm serve` covers
everything else: Claude Code, Cursor, Codex CLI, or your own agent in any
language. It loads one scenario, exposes its simulated services as tools, and
judges whatever the agent does with them using the same ledger, oracles and
validity rules as `apm run`. The output is the same `results.json` and
`report.html`.

```bash
pip install "agentpostmortem[mcp]"
apm serve --scenario APM-S001                       # MCP over stdio (default)
apm serve --scenario APM-S001 --transport http      # JSON over http://127.0.0.1:8765
```

| Option | Meaning |
| --- | --- |
| `--scenario ID` | The scenario to serve (one per server). `apm list` shows them. |
| `--transport mcp\|http` | MCP over stdio (default) or plain HTTP on 127.0.0.1. |
| `--port N` | HTTP port, default 8765. `0` picks a free port. |
| `--out DIR` | Where `results.json` and `report.html` go, default `apm-results`. |
| `--agent-name NAME` | Agent name in the results, default `external`. |
| `--exit-after-finish` | HTTP only: stop once the agent calls `finish`. |
| `--generated-at TS` | Pin the report timestamp, for reproducible files. |

Status lines go to stderr. With the MCP transport, stdout carries the protocol.

## What the agent sees

- **Instructions**: the scenario's task, its plan (which tool to call with which
  arguments, in order) and how errors and the clock work. MCP clients receive
  them as the server instructions; HTTP clients get them from `GET /tools`.
- **One tool per service operation in the plan**, named `service_operation`, for
  example `payments_refund`. The input schema comes from the operation's real
  parameters, with types taken from the service and the plan. Effectful
  operations accept an optional `idempotency_key`, as real APIs do.
- **`wait`** lets simulated time pass (`ticks`). The clock only moves on tool
  calls and on `wait`, so this is how an agent honours a rate limit's
  `retry_after`.
- **`finish`** ends the run with `status` (`completed` or `failed`), a `message`
  for the user and, optionally, `completed_steps`. This is the claim the honesty
  oracles judge. Call it exactly once.

Faults fire exactly as in `apm run`: a refund can time out after it went
through, a batch can partly fail, a read can be stale. Errors come back as JSON
with an `error` code (`timeout`, `server_error`, `rate_limit` with
`retry_after`, `not_found`, `invalid_request`, `conflict`). Over MCP they are
tool results with `isError` set.

Each call is mapped to a plan step for the report. When several plan steps share
a tool (three tickets, two emails), the step whose plan arguments match the call
wins.

## Claude Code

```bash
claude mcp add apm -- apm serve --scenario APM-S001 --agent-name claude-code --out apm-results
```

From a source checkout, run it through uv instead:

```bash
claude mcp add apm -- uv run --directory /path/to/agentpostmortem --extra mcp \
  apm serve --scenario APM-S001 --agent-name claude-code --out /tmp/apm-results
```

Then ask Claude Code to "do the task described by the apm server's instructions".
When it calls `finish`, the reports are written. If the session ends without a
`finish` call, they are written at shutdown and the run is marked invalid.

## Cursor, Codex CLI and other MCP clients

Any client that launches stdio MCP servers works. Cursor (`.cursor/mcp.json`):

```json
{
  "mcpServers": {
    "apm": {
      "command": "apm",
      "args": ["serve", "--scenario", "APM-S001", "--agent-name", "cursor"]
    }
  }
}
```

Codex CLI (`~/.codex/config.toml`):

```toml
[mcp_servers.apm]
command = "apm"
args = ["serve", "--scenario", "APM-S001", "--agent-name", "codex"]
```

## Plain HTTP

For agents that do not speak MCP. All bodies are JSON.

| Request | Answer |
| --- | --- |
| `GET /tools` | `{"scenario", "instructions", "tools": [{"name", "description", "input_schema"}]}` |
| `POST /tools/{name}` with the arguments object | `{"ok": true, "step", "result"}` or `{"ok": false, "step", "error", "message"}` |
| `POST /finish` with `{"status", "message"}` | the verdicts and validity of the run |
| `GET /results` | the `results.json` document for the run so far |

A failed tool call also sets the HTTP status the real service would: 504 for a
timeout, 500 for a server error, 429 for a rate limit, 404, 400 or 409. Requests
that never reach a service (unknown tool, wrong arguments, calls after
`finish`) answer 404, 400 or 409 with `error` set to `unknown_tool`,
`bad_arguments` or `finished`.

```bash
apm serve --scenario APM-S001 --transport http --port 8765 &
curl -s localhost:8765/tools
curl -s -X POST localhost:8765/tools/payments_refund -d '{"charge": "ch_0001", "amount": 2000}'
curl -s -X POST localhost:8765/finish -d '{"status": "completed", "message": "Refunded."}'
```

The server binds to 127.0.0.1 only and has no option to bind elsewhere. It
rejects requests whose `Host` or `Origin` header is not a loopback address, so a
web page open in your browser cannot drive it.

## Validity

The validity rules of `apm run` apply unchanged: a run where a declared fault
never fired is invalid, not a pass. Two rules are specific to external agents:

- **Crash scenarios are not supported yet.** The harness cannot kill a process
  it did not start, so the declared crash is never injected. Every crash
  scenario served this way (APM-S004, S012, S013, S014, S020, S023, S024, S025)
  is reported invalid with the reason "crash injection is not supported over MCP
  or HTTP". A restart protocol, where the client agrees to drop its in-memory
  state when told and resume from its own durable store, is a possible future
  addition.
- **A run without `finish` is invalid**, because there is no final claim to
  judge.

An external agent that makes the same calls as a reference agent gets the same
ledger, verdicts and validity as `apm run` gives that reference agent. The test
suite checks this for every non-crash scenario and every reference agent.

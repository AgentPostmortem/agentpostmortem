# AgentPostmortem for Hermes Agent

This directory connects AgentPostmortem to
[Hermes Agent](https://github.com/NousResearch/hermes-agent) by Nous Research:

- `skills/agentpostmortem/` is a Hermes skill. It lets a Hermes agent run the
  AgentPostmortem benchmark (`apm run ...`) from any surface Hermes supports
  (CLI, TUI, Telegram, Slack, Discord and the other gateway platforms) and explain
  which agents fail, on which incident pattern, and why.
- The section [Receiving AgentPostmortem alerts in Hermes](#receiving-agentpostmortem-alerts-in-hermes)
  shows how to set up a Hermes webhook route so benchmark alerts reach you in the
  chat app Hermes is already connected to.

The skill follows the `SKILL.md` format in the Hermes docs
([Creating Skills](https://hermes-agent.nousresearch.com/docs/developer-guide/creating-skills)):
YAML frontmatter (`name`, `description`, `version`, `author`, `license`,
`platforms`, `metadata.hermes.tags`, `metadata.hermes.requires_toolsets`) followed by
"When to Use", "Quick Reference", "Procedure", "Pitfalls" and "Verification"
sections, with a helper script under `scripts/` referenced through
`${HERMES_SKILL_DIR}`.

## Requirements

1. Hermes Agent, installed as the Hermes docs describe
   ([Installation](https://hermes-agent.nousresearch.com/docs/getting-started/installation)),
   for example:

   ```bash
   curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash
   ```

2. The `apm` command on the `PATH` of the machine where Hermes runs its terminal
   tool (Python 3.11 or newer):

   ```bash
   uv tool install git+https://github.com/AgentPostmortem/agentpostmortem
   apm --version
   ```

3. The `terminal` toolset enabled for the session. The skill declares
   `requires_toolsets: [terminal]`, so Hermes hides it where the terminal toolset
   is off.

The benchmark's default mode needs no model key and no network: the reference
agents are scripted, and the same command gives the same result every time.

## Install the skill

Pick one of the install paths the Hermes docs describe
([Skills System](https://hermes-agent.nousresearch.com/docs/user-guide/features/skills)).

**From GitHub, one skill (recommended).** Hermes installs a single skill from a
public repo path without adding a tap:

```bash
hermes skills install AgentPostmortem/agentpostmortem/integrations/hermes/skills/agentpostmortem
```

**As a tap.** `hermes skills tap add` assumes skills live under `skills/`, so after
adding the tap set its path to `integrations/hermes/skills/` in
`~/.hermes/skills/.hub/taps.json`:

```bash
hermes skills tap add AgentPostmortem/agentpostmortem
# then edit ~/.hermes/skills/.hub/taps.json:
#   {"taps": [{"repo": "AgentPostmortem/agentpostmortem", "path": "integrations/hermes/skills/"}]}
hermes skills tap list
hermes skills search agentpostmortem   # then install the identifier it prints
```

**From a local checkout.** Copy the skill into your Hermes skills directory, or
point Hermes at this directory with `skills.external_dirs` in
`~/.hermes/config.yaml`:

```bash
mkdir -p ~/.hermes/skills/testing
cp -r integrations/hermes/skills/agentpostmortem ~/.hermes/skills/testing/
```

```yaml
# ~/.hermes/config.yaml
skills:
  external_dirs:
    - /path/to/agentpostmortem/integrations/hermes/skills
```

Check that Hermes sees it:

```bash
hermes skills list
```

## Use it

Every installed skill is also a slash command, in the CLI and on every messaging
platform:

```text
/agentpostmortem run the benchmark and tell me which agent is safest
/agentpostmortem run APM-S001 and APM-S004 against the naive and careful agents
/agentpostmortem compare apm-results/results.json with last-week/results.json
```

Or from a shell, preloading the skill:

```bash
hermes chat --toolsets terminal,skills -s agentpostmortem \
  --oneshot -q "Run the AgentPostmortem benchmark and explain every failure"
```

What the agent does (see `skills/agentpostmortem/SKILL.md`):

1. Checks `apm --version`.
2. Runs `apm run --out apm-results`, which writes `results.json` and `report.html`.
3. Runs `scripts/apm_summary.py` on `results.json`, a standard-library script that
   keeps only totals, per-agent and per-category counts, regressions against an
   earlier run, and the failed oracles with their evidence.
4. Explains each failure as a consequence ("the refund was applied twice") and the
   fix it points to (idempotency keys, checkpoints, validating tool output).
5. On messaging platforms, attaches `report.html` with a `MEDIA:/absolute/path`
   line, which the Hermes gateway delivers as a file (`.html` is in the gateway's
   media allowlist, `MEDIA_DELIVERY_EXTS` in `gateway/platforms/base.py`).

## Receiving AgentPostmortem alerts in Hermes

AgentPostmortem's alerting (part of the paid engine and Cloud) can post a short
results summary (counts, regressions, report link) to a Hermes Agent gateway, and
Hermes forwards it to Telegram, Slack, Discord, Signal, email or any other platform
it is connected to. This uses the Hermes webhook adapter in **direct delivery
mode** ([Webhooks](https://hermes-agent.nousresearch.com/docs/user-guide/messaging/webhooks#direct-delivery-mode)):
no agent run, no LLM tokens, sub-second delivery.

1. Enable the webhook adapter in `~/.hermes/.env` and restart the gateway:

   ```bash
   WEBHOOK_ENABLED=true
   WEBHOOK_PORT=8644
   ```

2. Create a route. Either with the CLI:

   ```bash
   hermes webhook subscribe agentpostmortem \
     --deliver telegram \
     --deliver-chat-id "<your chat id>" \
     --deliver-only \
     --prompt "{text}" \
     --description "AgentPostmortem benchmark alerts"
   ```

   This prints the route URL and a masked secret; the full secret is stored in
   `~/.hermes/webhook_subscriptions.json`. Or in `~/.hermes/config.yaml`:

   ```yaml
   platforms:
     webhook:
       enabled: true
       extra:
         routes:
           agentpostmortem:
             secret: "<a long random secret>"
             signature: generic
             events: ["agentpostmortem.results"]
             deliver: telegram
             deliver_only: true
             prompt: "{text}"
             deliver_extra:
               chat_id: "<your chat id>"
   ```

3. Give AgentPostmortem the route URL and secret:

   ```bash
   APM_NOTIFY_HERMES_WEBHOOK_URL=http://127.0.0.1:8644/webhooks/agentpostmortem
   APM_NOTIFY_HERMES_SECRET=<the route secret>
   ```

What AgentPostmortem sends, so you can write your own route templates:

```json
{
  "event_type": "agentpostmortem.results",
  "text": "AgentPostmortem: 1 regression, 12/13 passed\nPassed last time, failing now:\n- APM-S001 (careful): Refund times out after it went through\nReport: https://...",
  "summary": {
    "label": "AgentPostmortem", "run_id": "...", "total": 13, "passed": 12, "failed": 1,
    "fixed": 0, "regressions": [{"scenario": "APM-S001", "agent": "careful", "title": "..."}],
    "link": "https://..."
  }
}
```

The request is signed with the Hermes generic V2 scheme: `X-Webhook-Timestamp`
(Unix seconds, accepted within 300 seconds of the gateway clock) and
`X-Webhook-Signature-V2`, the hex HMAC-SHA256 of `"<timestamp>.<body>"` under the
route secret. `X-Request-ID` carries a per-run id, which Hermes uses for its one
hour idempotency cache, so a retried alert is not delivered twice. A route created
with `hermes webhook subscribe` can be tested with
`hermes webhook test agentpostmortem --payload '{"event_type": "agentpostmortem.results", "text": "hello"}'`,
which signs the request the same way.

If you want Hermes to *reason* about the results instead of forwarding them, drop
`deliver_only` and write a `prompt` that uses `{summary.failed}`,
`{summary.regressions}` and `{summary.link}`. Webhook-triggered runs use a
restricted toolset by default (no terminal), which is what you want for an
inbound alert.

No gateway running? `hermes send --to telegram` delivers a one-off message using
the credentials Hermes already has, for bot-token platforms without a live gateway
([`hermes send`](https://hermes-agent.nousresearch.com/docs/reference/cli-commands#hermes-send)):

```bash
python3 integrations/hermes/skills/agentpostmortem/scripts/apm_summary.py \
  apm-results/results.json | hermes send --to telegram --subject "[AgentPostmortem]"
```

## Why a skill and not a plugin

The Hermes docs recommend a skill when a capability "wraps an external CLI or API
that the agent can call via `terminal` or `web_extract`" and a tool or plugin when it needs custom
Python inside the agent. Running `apm` is the first case, so the skill is the
right fit and it installs without enabling third-party code (Hermes plugins are
opt-in through `plugins.enabled`).

A plugin becomes the right tool for the next step: benchmarking a Hermes agent
itself. Hermes plugin hooks (`pre_tool_call`, `transform_tool_result`,
`post_tool_call`) sit exactly where AgentPostmortem needs to inject faults and
record side effects. That adapter is not in this directory yet.

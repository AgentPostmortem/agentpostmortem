"""Render a results document as one self-contained HTML page.

The page has no external assets and works without JavaScript. A few lines of
script only add "expand all" buttons and open a run when you follow a link to
it. Every piece of dynamic text goes through :func:`esc`.
"""

from __future__ import annotations

import html
import json
import re
from collections.abc import Iterable, Mapping
from typing import Any

from agentpostmortem.report.results import run_state

PASS_ICON = "✓"
FAIL_ICON = "✗"


def esc(value: object) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _label(value: object) -> str:
    text = str(value or "").replace("_", " ").strip()
    return text[:1].upper() + text[1:]


def _slug(value: object) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "-", str(value)).strip("-").lower() or "x"


def _as_list(value: object) -> list[Any]:
    return list(value) if isinstance(value, list | tuple) else []


def _as_map(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _rate(passed: int, total: int) -> int:
    return round(100 * passed / total) if total else 0


INVALID_ICON = "○"
_STATES = {
    "passed": ("pass", PASS_ICON, "PASS"),
    "failed": ("fail", FAIL_ICON, "FAIL"),
    "invalid": ("invalid", INVALID_ICON, "INVALID"),
}


def badge(passed: bool | str, *, small: bool = False) -> str:
    """A status badge. ``passed`` is a bool or a run state from ``run_state``."""
    state = passed if isinstance(passed, str) else ("passed" if passed else "failed")
    cls, icon, text = _STATES[state]
    size = " badge-sm" if small else ""
    return (
        f'<span class="badge {cls}{size}"><span class="icon" aria-hidden="true">{icon}</span>'
        f"{text}</span>"
    )


def _effect_badge(applied: bool) -> str:
    if applied:
        return (
            '<span class="badge applied"><span class="icon" aria-hidden="true">●</span>'
            "Applied</span>"
        )
    return (
        '<span class="badge dedup"><span class="icon" aria-hidden="true">↺</span>'
        "Deduplicated</span>"
    )


def _result_badge(result: object) -> str:
    text = str(result or "")
    if text == "ok":
        return (
            f'<span class="badge pass badge-sm"><span class="icon" aria-hidden="true">'
            f"{PASS_ICON}</span>ok</span>"
        )
    return (
        f'<span class="badge fail badge-sm"><span class="icon" aria-hidden="true">'
        f"{FAIL_ICON}</span>{esc(text or 'error')}</span>"
    )


def _table(
    caption: str, headers: Iterable[str], rows: Iterable[str], *, cls: str = "", label: str = ""
) -> str:
    head = "".join(f'<th scope="col">{h}</th>' for h in headers)
    region = f' role="region" aria-label="{esc(label)}" tabindex="0"' if label else ""
    return (
        f'<div class="scroll"{region}><table class="{cls}">'
        f'<caption class="sr-only">{esc(caption)}</caption>'
        f"<thead><tr>{head}</tr></thead><tbody>{''.join(rows)}</tbody></table></div>"
    )


def _bar(passed: int, total: int) -> str:
    rate = _rate(passed, total)
    return (
        f'<span class="rate"><span class="bar" aria-hidden="true">'
        f'<span style="width:{rate}%"></span></span><span class="num">{rate}%</span></span>'
    )


def _tally_rows(tally: Mapping[str, Any], *, labeller: Any = str) -> list[str]:
    rows = []
    for name, raw in tally.items():
        counts = _as_map(raw)
        passed = int(counts.get("passed", 0))
        failed = int(counts.get("failed", 0))
        invalid = int(counts.get("invalid", 0))
        rows.append(
            f'<tr><th scope="row">{esc(labeller(name))}</th>'
            f'<td class="n">{passed}</td><td class="n">{failed}</td>'
            f'<td class="n">{invalid or ""}</td>'
            f"<td>{_bar(passed, passed + failed)}</td></tr>"
        )
    return rows


def _summary_section(results: Mapping[str, Any]) -> str:
    summary = _as_map(results.get("summary"))
    total = int(summary.get("total", 0))
    passed = int(summary.get("passed", 0))
    failed = int(summary.get("failed", 0))
    invalid = int(summary.get("invalid", 0))
    stats = (
        f'<div class="stat"><span class="stat-label">Runs</span>'
        f'<span class="stat-value">{total}</span></div>'
        f'<div class="stat stat-pass"><span class="stat-label">'
        f'<span aria-hidden="true">{PASS_ICON}</span> Passed</span>'
        f'<span class="stat-value">{passed}</span></div>'
        f'<div class="stat stat-fail"><span class="stat-label">'
        f'<span aria-hidden="true">{FAIL_ICON}</span> Failed</span>'
        f'<span class="stat-value">{failed}</span></div>'
        + (
            f'<div class="stat stat-invalid"><span class="stat-label">'
            f'<span aria-hidden="true">{INVALID_ICON}</span> Invalid</span>'
            f'<span class="stat-value">{invalid}</span></div>'
            if invalid
            else ""
        )
        + f'<div class="stat"><span class="stat-label">Pass rate</span>'
        f'<span class="stat-value">{_rate(passed, passed + failed)}%</span></div>'
    )
    headers = ("{}", "Passed", "Failed", "Invalid", "Pass rate")
    by_agent = _table(
        "Passed and failed runs per agent",
        (headers[0].format("Agent"), *headers[1:]),
        _tally_rows(_as_map(summary.get("by_agent"))),
        cls="tally",
        label="Results by agent",
    )
    by_category = _table(
        "Passed and failed runs per scenario category",
        (headers[0].format("Category"), *headers[1:]),
        _tally_rows(_as_map(summary.get("by_category")), labeller=_label),
        cls="tally",
        label="Results by category",
    )
    return (
        '<section aria-labelledby="summary-h"><h2 id="summary-h">Summary</h2>'
        f'<div class="stats">{stats}</div>'
        '<div class="tallies">'
        f'<div class="card"><h3>By agent</h3>{by_agent}</div>'
        f'<div class="card"><h3>By category</h3>{by_category}</div>'
        "</div></section>"
    )


def _axes(results: Mapping[str, Any]) -> tuple[list[dict[str, str]], list[str]]:
    runs = [_as_map(r) for r in _as_list(results.get("runs"))]
    scenarios: dict[str, dict[str, str]] = {}
    for raw in _as_list(results.get("scenarios")):
        item = _as_map(raw)
        scenarios.setdefault(str(item.get("id", "")), {k: str(v) for k, v in item.items()})
    for run in runs:
        sid = str(run.get("scenario", ""))
        scenarios.setdefault(
            sid,
            {"id": sid, "title": str(run.get("title", "")), "category": str(run.get("category"))},
        )
    agents = [str(a) for a in _as_list(results.get("agents"))]
    for run in runs:
        if str(run.get("agent", "")) not in agents:
            agents.append(str(run.get("agent", "")))
    return list(scenarios.values()), agents


def _anchors(runs: list[Mapping[str, Any]]) -> list[str]:
    seen: set[str] = set()
    anchors = []
    for run in runs:
        base = f"run-{_slug(run.get('scenario'))}-{_slug(run.get('agent'))}"
        anchor, n = base, 2
        while anchor in seen:
            anchor, n = f"{base}-{n}", n + 1
        seen.add(anchor)
        anchors.append(anchor)
    return anchors


def _matrix_section(results: Mapping[str, Any], anchors: dict[tuple[str, str], str]) -> str:
    scenarios, agents = _axes(results)
    lookup: dict[tuple[str, str], str] = {}
    for raw in _as_list(results.get("runs")):
        run = _as_map(raw)
        lookup.setdefault((str(run.get("scenario")), str(run.get("agent"))), run_state(run))
    rows = []
    for scenario in scenarios:
        sid = scenario.get("id", "")
        cells = []
        for agent in agents:
            key = (sid, agent)
            if key in lookup:
                cells.append(
                    f'<td><a class="cell-link" href="#{esc(anchors[key])}" '
                    f'aria-label="{esc(sid)} with {esc(agent)}: '
                    f'{lookup[key]}">{badge(lookup[key])}</a></td>'
                )
            else:
                cells.append('<td><span class="muted">not run</span></td>')
        rows.append(
            f'<tr><th scope="row"><span class="sid">{esc(sid)}</span>'
            f'<span class="stitle">{esc(scenario.get("title", ""))}</span>'
            f'<span class="chip">{esc(_label(scenario.get("category", "")))}</span></th>'
            f"{''.join(cells)}</tr>"
        )
    table = _table(
        "Verdict for every scenario and agent",
        ("Scenario", *(f'<span class="mono">{esc(a)}</span>' for a in agents)),
        rows,
        cls="matrix",
        label="Scenario by agent matrix",
    )
    return (
        '<section aria-labelledby="matrix-h"><h2 id="matrix-h">Scenario matrix</h2>'
        '<p class="lede">Each cell is one run. A run passes only when every oracle passes. '
        "Select a cell to jump to its details.</p>"
        f"{table}</section>"
    )


def _verdicts(run: Mapping[str, Any]) -> str:
    items = []
    for raw in _as_list(run.get("verdicts")):
        verdict = _as_map(raw)
        evidence = "".join(f"<li>{esc(e)}</li>" for e in _as_list(verdict.get("evidence")))
        evidence_html = f'<ul class="evidence">{evidence}</ul>' if evidence else ""
        items.append(
            f'<li class="verdict">{badge(bool(verdict.get("passed")), small=True)}'
            f'<div><code class="oracle">{esc(verdict.get("oracle"))}</code>'
            f'<p class="msg">{esc(verdict.get("message"))}</p>{evidence_html}</div></li>'
        )
    if not items:
        return '<p class="muted">No oracle verdicts.</p>'
    return f'<ul class="verdicts">{"".join(items)}</ul>'


def _timeline(run: Mapping[str, Any]) -> str:
    rows = []
    for raw in _as_list(run.get("calls")):
        call = _as_map(raw)
        attempt = call.get("attempt")
        retry = ' class="retry"' if isinstance(attempt, int) and attempt > 1 else ""
        rows.append(
            f"<tr{retry}><td class='n'>{esc(call.get('seq'))}</td>"
            f"<td>{esc(call.get('step'))}</td>"
            f'<td class="mono">{esc(call.get("service"))}.{esc(call.get("operation"))}</td>'
            f"<td class='n'>{esc(attempt)}</td>"
            f"<td>{_result_badge(call.get('result'))}</td></tr>"
        )
    if not rows:
        return '<p class="muted">The agent made no tool calls.</p>'
    return _table(
        "Tool calls in order", ("#", "Step", "Call", "Attempt", "Result"), rows, cls="data"
    )


def _payload(value: object) -> str:
    if not value:
        return ""
    try:
        return json.dumps(value, sort_keys=True, separators=(", ", ": "), ensure_ascii=False)
    except (TypeError, ValueError):
        return str(value)


def _ledger(run: Mapping[str, Any]) -> str:
    rows = []
    for raw in _as_list(run.get("ledger")):
        effect = _as_map(raw)
        applied = bool(effect.get("applied", True))
        key = effect.get("idempotency_key")
        key_html = f"<code>{esc(key)}</code>" if key else '<span class="muted">none</span>'
        rows.append(
            f'<tr class="{"" if applied else "deduped"}">'
            f'<td class="n">{esc(effect.get("seq"))}</td>'
            f"<td>{_effect_badge(applied)}</td>"
            f'<td class="mono">{esc(effect.get("service"))}.{esc(effect.get("operation"))}</td>'
            f"<td class='mono'>{esc(effect.get('subject'))}</td>"
            f"<td>{key_html}</td>"
            f'<td class="mono payload">{esc(_payload(effect.get("payload")))}</td></tr>'
        )
    if not rows:
        return '<p class="muted">No side effects were recorded.</p>'
    return _table(
        "Side effects recorded by the ledger",
        ("#", "State", "Effect", "Subject", "Idempotency key", "Payload"),
        rows,
        cls="data",
    )


def _restarts(value: object) -> str:
    if not isinstance(value, int) or value < 1:
        return ""
    return f" · {value} restart{'' if value == 1 else 's'}"


def _run_section(run: Mapping[str, Any], anchor: str) -> str:
    state = run_state(run)
    reasons = [str(r) for r in _as_list(run.get("invalid_reasons"))]
    invalid_note = (
        '<p class="invalid-note"><strong>Not counted.</strong> This run did not exercise '
        "what the scenario declares: " + esc("; ".join(reasons)) + ".</p>"
        if state == "invalid"
        else ""
    )
    incident = _as_map(run.get("incident"))
    outcome = _as_map(run.get("outcome"))
    verdicts = [_as_map(v) for v in _as_list(run.get("verdicts"))]
    failing = sum(1 for v in verdicts if not v.get("passed"))
    calls = len(_as_list(run.get("calls")))
    effects = _as_list(run.get("ledger"))
    deduped = sum(1 for e in effects if not _as_map(e).get("applied", True))
    facts = (
        f"{len(verdicts) - failing}/{len(verdicts)} oracles passed · {calls} calls · "
        f"{len(effects)} effects"
        + (f" ({deduped} deduplicated)" if deduped else "")
        + _restarts(run.get("restarts"))
    )

    refs = "".join(f"<li>{esc(r)}</li>" for r in _as_list(incident.get("references")))
    story = ""
    if incident:
        story = (
            f'<p class="pattern">{esc(incident.get("pattern"))}</p>'
            f"<p>{esc(incident.get('summary'))}</p>"
            + (f'<ul class="refs">{refs}</ul>' if refs else "")
        )
    else:
        story = '<p class="muted">No incident story in this results file.</p>'
    task = f"<p>{esc(run.get('task'))}</p>" if run.get("task") else ""
    status = str(outcome.get("status", ""))
    steps = ", ".join(str(s) for s in _as_list(outcome.get("completed_steps")))
    outcome_html = (
        f'<dl class="facts"><dt>Status</dt><dd><span class="status">{esc(status)}</span></dd>'
        f"<dt>Claim</dt><dd>“{esc(outcome.get('claim'))}”</dd>"
        f'<dt>Steps done</dt><dd class="mono">{esc(steps) or "none"}</dd>'
        f"<dt>Restarts</dt><dd>{esc(run.get('restarts', 0))}</dd></dl>"
    )
    return (
        f'<details class="run is-{_STATES[state][0]}" id="{esc(anchor)}">'
        f"<summary>{badge(state)}"
        f'<span class="run-title"><span class="sid">{esc(run.get("scenario"))}</span>'
        f'<span class="run-agent">{esc(run.get("agent"))}</span>'
        f'<span class="stitle">{esc(run.get("title"))}</span></span>'
        f'<span class="run-facts">{facts}</span></summary>'
        f'<div class="run-body">{invalid_note}'
        '<div class="story">'
        f"<div><h4>Incident</h4>{story}</div>"
        f"<div><h4>Task</h4>{task}<h4>Agent outcome</h4>{outcome_html}</div>"
        "</div>"
        f"<h4>Verdicts</h4>{_verdicts(run)}"
        f"<h4>Tool-call timeline</h4>{_timeline(run)}"
        f"<h4>Ledger</h4>{_ledger(run)}"
        "</div></details>"
    )


CSS = """
:root{
  color-scheme:light dark;
  --font:system-ui,-apple-system,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif;
  --mono:ui-monospace,SFMono-Regular,"SF Mono",Menlo,Consolas,"Liberation Mono",monospace;
  --bg:#f6f6f3;--surface:#ffffff;--surface-2:#f0f0ec;--text:#16181b;--muted:#565d66;
  --border:#e0e0da;--border-strong:#c9c9c1;--accent:#2f4fd1;--focus:#2f4fd1;
  --pass:#11643a;--pass-bg:#e3f3e8;--pass-border:#b5dcc2;
  --fail:#a3221b;--fail-bg:#fde9e7;--fail-border:#f2bab4;
  --dedup:#7c4a00;--dedup-bg:#fcf0d6;--dedup-border:#ecd39b;
  --shadow:0 1px 2px rgba(20,22,26,.05),0 4px 16px rgba(20,22,26,.04);
  --radius:12px;--radius-sm:8px;
  --s1:4px;--s2:8px;--s3:12px;--s4:16px;--s5:24px;--s6:32px;--s7:48px;
}
@media (prefers-color-scheme:dark){
  :root{
    --bg:#0e1013;--surface:#16191e;--surface-2:#1d2128;--text:#e7e9ec;--muted:#9da5af;
    --border:#272c34;--border-strong:#363c46;--accent:#8ea6ff;--focus:#8ea6ff;
    --pass:#79dba0;--pass-bg:rgba(56,170,100,.14);--pass-border:rgba(121,219,160,.32);
    --fail:#ff9a90;--fail-bg:rgba(230,80,70,.14);--fail-border:rgba(255,154,144,.32);
    --dedup:#f3c46e;--dedup-bg:rgba(230,170,60,.13);--dedup-border:rgba(243,196,110,.32);
    --shadow:0 1px 2px rgba(0,0,0,.3);
  }
}
*,*::before,*::after{box-sizing:border-box}
html{-webkit-text-size-adjust:100%;text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--text);font:15px/1.55 var(--font);
  overflow-wrap:break-word}
.wrap{max-width:1160px;margin:0 auto;padding:0 var(--s4)}
@media (min-width:720px){.wrap{padding:0 var(--s6)}}
a{color:var(--accent)}
:focus-visible{outline:3px solid var(--focus);outline-offset:2px;border-radius:4px}
code,.mono{font-family:var(--mono);font-size:.86em}
.sr-only{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;
  clip:rect(0 0 0 0);white-space:nowrap;border:0}
.skip{position:absolute;left:var(--s4);top:-48px;background:var(--surface);
  padding:var(--s2) var(--s3);
  border-radius:var(--radius-sm);z-index:2}
.skip:focus{top:var(--s2)}
.muted{color:var(--muted)}
header.site{border-bottom:1px solid var(--border);background:var(--surface);
  padding:var(--s6) 0 var(--s5)}
.eyebrow{margin:0 0 var(--s2);font:600 12px/1 var(--mono);letter-spacing:.08em;
  text-transform:uppercase;
  color:var(--muted)}
h1{margin:0;font-size:clamp(26px,4vw,34px);line-height:1.15;letter-spacing:-.02em}
.meta{margin:var(--s3) 0 0;color:var(--muted);display:flex;flex-wrap:wrap;
  gap:var(--s1) var(--s4)}
.meta span{white-space:nowrap}
main{padding:var(--s6) 0 var(--s7)}
section{margin-top:var(--s6)}
section:first-child{margin-top:0}
h2{font-size:20px;letter-spacing:-.01em;margin:0 0 var(--s3)}
h3{font-size:13px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);
  margin:0 0 var(--s3)}
h4{font-size:13px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);
  margin:var(--s5) 0 var(--s2)}
.lede{margin:0 0 var(--s4);color:var(--muted);max-width:70ch}
.stats{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:var(--s3)}
@media (min-width:720px){.stats{grid-template-columns:repeat(4,minmax(0,1fr))}}
.stat,.card{background:var(--surface);border:1px solid var(--border);
  border-radius:var(--radius);
  box-shadow:var(--shadow)}
.stat{padding:var(--s4);display:flex;flex-direction:column;gap:var(--s1)}
.stat-label{font-size:13px;color:var(--muted)}
.stat-value{font-size:30px;font-weight:650;letter-spacing:-.02em;line-height:1.1;
  font-variant-numeric:tabular-nums}
.stat-pass .stat-label,.stat-pass .stat-value{color:var(--pass)}
.stat-fail .stat-label,.stat-fail .stat-value{color:var(--fail)}
.tallies{display:grid;gap:var(--s3);margin-top:var(--s3)}
@media (min-width:900px){.tallies{grid-template-columns:1fr 1fr}}
.card{padding:var(--s4) var(--s4) var(--s2);min-width:0}
.scroll{overflow-x:auto;max-width:100%;-webkit-overflow-scrolling:touch}
table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums}
th,td{text-align:left;padding:var(--s2) var(--s3);border-bottom:1px solid var(--border);
  vertical-align:top}
thead th{font-size:12px;font-weight:600;color:var(--muted);text-transform:uppercase;
  letter-spacing:.05em;
  white-space:nowrap;border-bottom-color:var(--border-strong)}
tbody tr:last-child>*{border-bottom:0}
td.n,th.n{text-align:right;width:1%;white-space:nowrap}
.tally th[scope=row]{font-weight:500}
.tally td,.tally th{padding-left:0}
.tally td:last-child{width:40%;min-width:120px}
.rate{display:flex;align-items:center;gap:var(--s2)}
.bar{flex:1;height:6px;border-radius:99px;background:var(--fail-bg);overflow:hidden;
  min-width:48px;
  box-shadow:inset 0 0 0 1px var(--fail-border)}
.bar span{display:block;height:100%;background:var(--pass);border-radius:99px}
.rate .num{font-size:13px;color:var(--muted);min-width:4ch;text-align:right}
.matrix-wrap,.matrix-wrap .scroll{border-radius:var(--radius)}
section > .scroll{background:var(--surface);border:1px solid var(--border);
  border-radius:var(--radius);
  box-shadow:var(--shadow)}
.matrix{min-width:640px}
.matrix th[scope=row]{min-width:220px;max-width:340px;font-weight:400}
.matrix td{vertical-align:middle;white-space:nowrap}
.matrix thead th:not(:first-child){text-transform:none;letter-spacing:0;font-size:13px;
  color:var(--text)}
.sid{display:block;font:600 13px/1.4 var(--mono)}
.stitle{display:block}
.chip{display:inline-block;margin-top:var(--s1);font-size:12px;color:var(--muted);
  background:var(--surface-2);
  border:1px solid var(--border);border-radius:99px;padding:0 var(--s2)}
.cell-link{display:inline-block;text-decoration:none;border-radius:99px}
.cell-link:hover .badge{filter:brightness(.96);box-shadow:0 0 0 2px currentColor inset}
.badge{display:inline-flex;align-items:center;gap:6px;font:700 12px/1 var(--mono);
  letter-spacing:.04em;
  padding:6px 10px;border-radius:99px;border:1px solid transparent;white-space:nowrap}
.badge-sm{padding:3px 8px;font-size:11.5px}
.badge .icon{font-family:var(--font);font-weight:700}
.badge.pass,.badge.applied{color:var(--pass);background:var(--pass-bg);
  border-color:var(--pass-border)}
.badge.fail{color:var(--fail);background:var(--fail-bg);border-color:var(--fail-border)}
.badge.invalid{color:var(--muted);background:transparent;border-color:var(--border-strong);border-style:dashed}
.stat-invalid .stat-label,.stat-invalid .stat-value{color:var(--muted)}
details.run.is-invalid{border-left-color:var(--border-strong)}
.invalid-note{margin:0 0 14px;padding:10px 12px;border:1px dashed var(--border-strong);
  border-radius:8px;color:var(--muted)}
.badge.dedup{color:var(--dedup);background:var(--dedup-bg);border-color:var(--dedup-border)}
.badge.applied,.badge.dedup{font-weight:600;letter-spacing:0}
.runs-head{display:flex;flex-wrap:wrap;align-items:baseline;justify-content:space-between;
  gap:var(--s2)}
.tools{display:flex;gap:var(--s2)}
.tools button{font:500 13px/1 var(--font);color:var(--text);background:var(--surface);
  border:1px solid var(--border-strong);border-radius:var(--radius-sm);padding:7px 12px;
  cursor:pointer}
.tools button:hover{background:var(--surface-2)}
details.run{background:var(--surface);border:1px solid var(--border);
  border-left:4px solid var(--pass);
  border-radius:var(--radius);box-shadow:var(--shadow);margin-top:var(--s3)}
details.run.is-fail{border-left-color:var(--fail)}
details.run>summary{list-style:none;cursor:pointer;display:grid;
  grid-template-columns:auto 1fr auto;
  align-items:start;gap:var(--s2) var(--s3);padding:var(--s3) var(--s4);
  border-radius:var(--radius)}
details.run>summary::-webkit-details-marker{display:none}
details.run>summary::after{content:"";grid-column:3;grid-row:1;width:9px;height:9px;
  margin-top:8px;
  border-right:2px solid var(--muted);border-bottom:2px solid var(--muted);
  transform:rotate(45deg);
  transition:transform .15s ease}
details.run[open]>summary::after{transform:rotate(-135deg);margin-top:12px}
details.run>summary:hover{background:var(--surface-2)}
.run-title{display:flex;flex-wrap:wrap;align-items:baseline;gap:0 var(--s2);min-width:0}
.run-title .sid{display:inline}
.run-agent{font:600 13px/1.4 var(--mono);color:var(--accent)}
.run-title .stitle{flex-basis:100%}
.run-facts{grid-column:2/4;font-size:13px;color:var(--muted)}
@media (min-width:720px){details.run>summary .badge{margin-top:1px}}
.run-body{padding:0 var(--s4) var(--s4);border-top:1px solid var(--border)}
.story{display:grid;gap:0 var(--s6)}
@media (min-width:820px){.story{grid-template-columns:1.2fr 1fr}}
.story p{margin:0 0 var(--s2);max-width:70ch}
.pattern{font-weight:600}
.refs{margin:0;padding-left:var(--s5);font-size:14px}
dl.facts{display:grid;grid-template-columns:max-content 1fr;gap:var(--s1) var(--s4);margin:0}
dl.facts dt{color:var(--muted);font-size:14px}
dl.facts dd{margin:0;min-width:0}
.status{font:600 13px/1.4 var(--mono);text-transform:uppercase;letter-spacing:.04em}
ul.verdicts{list-style:none;margin:0;padding:0;display:grid;gap:var(--s2)}
.verdict{display:grid;grid-template-columns:auto 1fr;gap:var(--s3);align-items:start;
  padding:var(--s3);
  background:var(--surface-2);border:1px solid var(--border);border-radius:var(--radius-sm)}
.verdict .badge{margin-top:2px;justify-self:start}
@media (max-width:520px){.verdict{grid-template-columns:1fr;
  gap:var(--s2)}.run-body{padding:0 var(--s3) var(--s3)}}
.verdict>div{min-width:0}
.oracle{font-weight:600}
.msg{margin:2px 0 0}
.evidence{margin:var(--s2) 0 0;padding:var(--s2) var(--s3);list-style:none;
  font:13px/1.6 var(--mono);
  background:var(--surface);border:1px solid var(--border);border-radius:6px;color:var(--muted);
  overflow-wrap:anywhere}
.run-body .scroll{border:1px solid var(--border);border-radius:var(--radius-sm)}
table.data{min-width:520px;font-size:14px}
table.data td{vertical-align:middle}
table.data thead th{background:var(--surface-2)}
table.data td.payload{color:var(--muted);min-width:180px;overflow-wrap:anywhere}
tr.retry td:first-child{box-shadow:inset 3px 0 0 var(--border-strong)}
tr.deduped td{background:var(--dedup-bg)}
tr.deduped td:first-child{box-shadow:inset 3px 0 0 var(--dedup)}
.legend{margin:var(--s2) 0 0;font-size:13px;color:var(--muted)}
footer{border-top:1px solid var(--border);padding:var(--s5) 0 var(--s6);color:var(--muted);
  font-size:13px}
@media (prefers-reduced-motion:reduce){*{transition:none!important}}
@media print{details.run{break-inside:avoid}.tools{display:none}}
""".strip()

SCRIPT = """
(function(){
  function openTarget(){
    var id=decodeURIComponent(location.hash.slice(1));
    var el=id&&document.getElementById(id);
    if(el&&el.tagName==="DETAILS"){el.open=true;}
  }
  window.addEventListener("hashchange",openTarget);openTarget();
  var bar=document.getElementById("run-tools");if(!bar)return;
  function all(open){document.querySelectorAll("details.run").forEach(function(d){d.open=open;});}
  [["Expand all",true],["Collapse all",false]].forEach(function(spec){
    var b=document.createElement("button");b.type="button";b.textContent=spec[0];
    b.addEventListener("click",function(){all(spec[1]);});bar.appendChild(b);
  });
})();
""".strip()


def render_html(results: Mapping[str, Any]) -> str:
    """Render a results document. Output depends only on ``results``."""
    runs = [_as_map(r) for r in _as_list(results.get("runs"))]
    anchor_list = _anchors(runs)
    anchors: dict[tuple[str, str], str] = {}
    for run, anchor in zip(runs, anchor_list, strict=True):
        anchors.setdefault((str(run.get("scenario")), str(run.get("agent"))), anchor)
    scenarios, agents = _axes(results)
    tool = _as_map(results.get("tool"))
    generated = results.get("generated_at", "")
    run_sections = "".join(
        _run_section(run, anchor) for run, anchor in zip(runs, anchor_list, strict=True)
    )
    if not runs:
        run_sections = '<p class="muted">This results file has no runs.</p>'
    meta = (
        f"<span>{len(scenarios)} scenarios &times; {len(agents)} agents</span>"
        f"<span>Generated <time>{esc(generated)}</time></span>"
        f"<span>{esc(tool.get('name', 'agentpostmortem'))} {esc(tool.get('version', ''))}</span>"
    )
    return (
        "<!doctype html>\n"
        '<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta name="color-scheme" content="light dark">'
        f"<title>AgentPostmortem report · {esc(generated)}</title>"
        f"<style>{CSS}</style></head><body>"
        '<a class="skip" href="#main">Skip to results</a>'
        '<header class="site"><div class="wrap">'
        '<p class="eyebrow">AgentPostmortem</p><h1>Reliability report</h1>'
        f'<p class="meta">{meta}</p></div></header>'
        '<main id="main" class="wrap">'
        f"{_summary_section(results)}"
        f"{_matrix_section(results, anchors)}"
        '<section aria-labelledby="runs-h"><div class="runs-head">'
        '<h2 id="runs-h">Runs</h2><div class="tools" id="run-tools"></div></div>'
        '<p class="lede">Open a run to see the incident it reproduces, what each oracle '
        "concluded, every tool call the agent made, and every side effect the ledger "
        "recorded. Retried calls and deduplicated effects are highlighted and labelled.</p>"
        f"{run_sections}</section></main>"
        '<footer><div class="wrap">Made with AgentPostmortem. Results come from simulated '
        "services; no real money moved and no real email was sent.</div></footer>"
        f"<script>{SCRIPT}</script></body></html>\n"
    )

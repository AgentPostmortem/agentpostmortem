"""Push an ``apm`` results document to Opik as traces.

Optional integration. The ``opik`` package is imported only when a reporter is
created, so the benchmark keeps working without the extra installed.

One trace per run:

- trace name ``apm:<scenario id>``
- one span of type ``tool`` per tool call, named ``<service>.<operation>``
- one span of type ``general`` per injected fault, tagged ``fault``
- feedback score ``passed`` (1.0 or 0.0)
- feedback score ``valid`` (1.0 or 0.0), so runs that never fired their
  declared fault are separable from real failures
- metadata: agent, category, restarts, verdicts and the final ledger
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Protocol

PASSED_SCORE = "passed"
VALID_SCORE = "valid"
FAULT_TAG = "fault"


class OpikReportError(RuntimeError):
    """The Opik extra is missing, or the results document cannot be reported."""


class SpanLike(Protocol):
    def end(self) -> None: ...


class TraceLike(Protocol):
    def span(self, **kwargs: Any) -> SpanLike: ...

    def log_feedback_score(self, *, name: str, value: float, reason: str | None = None) -> None: ...

    def end(self) -> None: ...


class ClientLike(Protocol):
    def trace(self, **kwargs: Any) -> TraceLike: ...

    def flush(self, timeout: int | None = None) -> Any: ...


def _import_opik() -> Any:
    try:
        import opik  # noqa: PLC0415
    except ImportError as exc:
        raise OpikReportError(
            "the Opik reporter needs the opik extra: pip install 'agentpostmortem-bench[opik]'"
        ) from exc
    return opik


def trace_name(run: Mapping[str, Any]) -> str:
    return f"apm:{run.get('scenario')}"


def run_tags(run: Mapping[str, Any]) -> list[str]:
    tags = ["agentpostmortem", str(run.get("category") or ""), str(run.get("agent") or "")]
    if run.get("valid") is False:
        tags.append("invalid")
    return [tag for tag in tags if tag]


def score_reason(run: Mapping[str, Any]) -> str:
    invalid = run.get("invalid_reasons") or []
    if run.get("valid") is False and invalid:
        return "invalid: " + "; ".join(str(reason) for reason in invalid)
    verdicts = run.get("verdicts") or []
    for verdict in verdicts:
        if isinstance(verdict, Mapping) and not verdict.get("passed"):
            return str(verdict.get("message") or "failed")
    return "all oracles passed"


def run_metadata(run: Mapping[str, Any]) -> dict[str, Any]:
    meta = run.get("meta") or {}
    metadata: dict[str, Any] = {
        "scenario": run.get("scenario"),
        "title": run.get("title"),
        "category": run.get("category"),
        "agent": run.get("agent"),
        "passed": bool(run.get("passed")),
        "valid": run.get("valid") is not False,
        "invalid_reasons": list(run.get("invalid_reasons") or []),
        "restarts": run.get("restarts", 0),
        "verdicts": list(run.get("verdicts") or []),
        "ledger": list(run.get("ledger") or []),
        "framework": meta.get("framework"),
        "framework_version": meta.get("framework_version"),
    }
    return {key: value for key, value in metadata.items() if value is not None}


class OpikReporter:
    """Write a results document to Opik. Pass ``client`` to supply your own."""

    def __init__(self, project_name: str, client: ClientLike | None = None) -> None:
        if not project_name:
            raise ValueError("project_name must not be empty")
        self.project_name = project_name
        if client is None:
            opik = _import_opik()
            # batching=False: spans are created and ended back-to-back, so the
            # batched create/update ordering can drop data. Disabling batching
            # makes each call synchronous and removes the risk.
            client = opik.Opik(project_name=project_name, batching=False)
        self.client: ClientLike = client

    def emit(self, results: Mapping[str, Any]) -> int:
        runs = results.get("runs")
        if not isinstance(runs, Sequence) or isinstance(runs, (str, bytes)):
            raise OpikReportError("results document has no runs list")
        for run in runs:
            if not isinstance(run, Mapping):
                raise OpikReportError("results document contains a run that is not an object")
            self._emit_run(run)
        self.client.flush()
        return len(runs)

    def _emit_run(self, run: Mapping[str, Any]) -> None:
        outcome = run.get("outcome") or {}
        trace = self.client.trace(
            name=trace_name(run),
            input={"scenario": run.get("scenario"), "task": run.get("task")},
            output={"status": outcome.get("status"), "claim": outcome.get("claim")},
            metadata=run_metadata(run),
            tags=run_tags(run),
            project_name=self.project_name,
        )

        for call in run.get("calls") or []:
            span = trace.span(
                name=f"{call.get('service')}.{call.get('operation')}",
                type="tool",
                input={
                    "step": call.get("step"),
                    "attempt": call.get("attempt"),
                    "tick": call.get("tick"),
                },
                output={"result": call.get("result")},
                metadata={"seq": call.get("seq")},
                tags=["tool_call"],
            )
            span.end()

        for fault in run.get("faults") or []:
            span = trace.span(
                name=f"fault:{fault.get('service')}.{fault.get('operation')}",
                type="general",
                output={"fired": fault.get("fired")},
                metadata={"kind": fault.get("kind"), "fired": fault.get("fired")},
                tags=[FAULT_TAG, str(fault.get("kind") or "")],
            )
            span.end()

        reason = score_reason(run)
        trace.log_feedback_score(
            name=PASSED_SCORE, value=1.0 if run.get("passed") else 0.0, reason=reason
        )
        trace.log_feedback_score(
            name=VALID_SCORE,
            value=0.0 if run.get("valid") is False else 1.0,
            reason=reason if run.get("valid") is False else None,
        )
        trace.end()


def report_to_opik(
    results: Mapping[str, Any], project_name: str, client: ClientLike | None = None
) -> int:
    """Convenience wrapper: build a reporter, emit, return the trace count."""
    return OpikReporter(project_name, client=client).emit(results)

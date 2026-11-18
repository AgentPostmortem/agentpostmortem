"""Simulated service base class with deterministic fault injection."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Mapping
from typing import Any

from agentpostmortem.domain.faults import FaultKind, FaultSpec
from agentpostmortem.ledger import Ledger
from agentpostmortem.sims.errors import RateLimited, ServerError, ToolTimeout

Handler = Callable[..., dict[str, Any]]


class Corruption:
    """Hooks a service implements to describe its own bad responses."""

    def malformed(self, operation: str, response: dict[str, Any]) -> dict[str, Any]:
        """Default: drop every field. Services override with realistic breakage."""
        return {}

    def stale(
        self, operation: str, response: dict[str, Any], params: Mapping[str, Any]
    ) -> dict[str, Any]:
        return response

    def lying(
        self, operation: str, response: dict[str, Any], params: Mapping[str, Any]
    ) -> dict[str, Any]:
        return response


class SimService(Corruption):
    """A deterministic in-process service whose operations can be made to fail.

    Subclasses register operations in ``operations``. Every call goes through
    :meth:`call`, which counts calls per operation and applies the faults the
    scenario declared for that call number.
    """

    name: str = "service"

    def __init__(self, ledger: Ledger, faults: list[FaultSpec] | None = None) -> None:
        self.ledger = ledger
        self._faults = [f for f in (faults or []) if f.service == self.name]
        self._calls: defaultdict[str, int] = defaultdict(int)
        self.operations: dict[str, Handler] = {}

    def seed(self, state: Mapping[str, Any]) -> None:
        """Load initial state. Services override when they have state."""

    def calls(self, operation: str) -> int:
        return self._calls[operation]

    def call(self, operation: str, **kwargs: Any) -> dict[str, Any]:
        if operation not in self.operations:
            raise ValueError(f"{self.name} has no operation {operation!r}")
        self._calls[operation] += 1
        number = self._calls[operation]
        self.ledger.clock.tick()
        fault = next(
            (f for f in self._faults if f.operation == operation and f.trigger.fires(number)), None
        )
        if fault is None:
            return self.operations[operation](**kwargs)
        return self._apply(fault, operation, kwargs)

    def _apply(self, fault: FaultSpec, operation: str, kwargs: dict[str, Any]) -> dict[str, Any]:
        kind = fault.kind
        if kind is FaultKind.TIMEOUT:
            raise ToolTimeout(f"{self.name}.{operation} timed out")
        if kind is FaultKind.SERVER_ERROR:
            raise ServerError(f"{self.name}.{operation} returned 500")
        if kind is FaultKind.RATE_LIMIT:
            raise RateLimited(int(fault.params.get("retry_after", 5)))
        response = self.operations[operation](**kwargs)
        if kind is FaultKind.TIMEOUT_AFTER_SUCCESS:
            raise ToolTimeout(f"{self.name}.{operation} timed out after the request was accepted")
        if kind is FaultKind.MALFORMED:
            return self.malformed(operation, response)
        if kind is FaultKind.STALE:
            return self.stale(operation, response, fault.params)
        if kind is FaultKind.LYING:
            return self.lying(operation, response, fault.params)
        raise AssertionError(f"unhandled fault kind {kind}")

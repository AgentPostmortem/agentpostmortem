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


class Quota:
    """A fixed-window rate limit on one operation, measured in clock ticks.

    At most ``limit`` calls are accepted in each window of ``window`` ticks.
    A rejected call raises :class:`RateLimited` with the ticks left until the
    window resets, which is what a well behaved client should wait.
    """

    def __init__(self, limit: int, window: int) -> None:
        if limit < 1 or window < 1:
            raise ValueError("quota limit and window must be positive")
        self.limit = limit
        self.window = window
        self._used: defaultdict[int, int] = defaultdict(int)

    def admit(self, now: int) -> None:
        bucket = now // self.window
        if self._used[bucket] >= self.limit:
            raise RateLimited(self.window - now % self.window)
        self._used[bucket] += 1


class SimService(Corruption):
    """A deterministic in-process service whose operations can be made to fail.

    Subclasses register operations in ``operations``. Every call goes through
    :meth:`call`, which counts calls per operation, enforces any quota declared
    in the seed, and applies the faults the scenario declared for that call
    number.
    """

    name: str = "service"

    def __init__(self, ledger: Ledger, faults: list[FaultSpec] | None = None) -> None:
        self.ledger = ledger
        self._faults = [f for f in (faults or []) if f.service == self.name]
        self._calls: defaultdict[str, int] = defaultdict(int)
        self.operations: dict[str, Handler] = {}
        self.quotas: dict[str, Quota] = {}

    def seed(self, state: Mapping[str, Any]) -> None:
        """Load initial state. Services override when they have state."""
        self.load_quotas(state)

    def load_quotas(self, state: Mapping[str, Any]) -> None:
        """Read ``quota: {operation: {limit, window}}`` from a seed."""
        for operation, spec in dict(state.get("quota", {})).items():
            self.quotas[str(operation)] = Quota(int(spec["limit"]), int(spec["window"]))

    def calls(self, operation: str) -> int:
        return self._calls[operation]

    def call(self, operation: str, **kwargs: Any) -> dict[str, Any]:
        if operation not in self.operations:
            raise ValueError(f"{self.name} has no operation {operation!r}")
        self._calls[operation] += 1
        number = self._calls[operation]
        self.ledger.clock.tick()
        if operation in self.quotas:
            self.quotas[operation].admit(self.ledger.clock.now)
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

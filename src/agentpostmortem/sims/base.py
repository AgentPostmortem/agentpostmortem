"""Simulated service base class with deterministic fault injection."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Mapping
from typing import Any

from agentpostmortem.domain.faults import FaultKind, FaultSpec
from agentpostmortem.ledger import Ledger
from agentpostmortem.sims.errors import RateLimited, ServerError, ToolError, ToolTimeout

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

    def partial(
        self, operation: str, kwargs: dict[str, Any], params: Mapping[str, Any]
    ) -> dict[str, Any]:
        """Run a batch operation with some items failing. Batch services override."""
        raise ValueError(f"{operation} is not a batch operation; it cannot fail partially")


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
    :meth:`call`, which counts calls per operation and applies the faults the
    scenario declared for that call number.

    A ``rate_limit`` fault whose params set ``limit`` and ``window`` is a
    quota rather than a one-off rejection: calls it covers are accepted until
    the window is full, then rejected with the ticks left until it resets.
    """

    name: str = "service"

    def __init__(self, ledger: Ledger, faults: list[FaultSpec] | None = None) -> None:
        self.ledger = ledger
        self._faults = [f for f in (faults or []) if f.service == self.name]
        self._calls: defaultdict[str, int] = defaultdict(int)
        self._fired: defaultdict[int, int] = defaultdict(int)
        self.operations: dict[str, Handler] = {}
        self._quotas = {
            index: Quota(int(f.params["limit"]), int(f.params["window"]))
            for index, f in enumerate(self._faults)
            if is_quota(f)
        }

    def seed(self, state: Mapping[str, Any]) -> None:
        """Load initial state. Services override when they have state."""

    def calls(self, operation: str) -> int:
        return self._calls[operation]

    def fault_activity(self) -> list[tuple[FaultSpec, int]]:
        """Each declared fault with how many calls it actually affected.

        A quota counts only the calls it rejected. A run where a declared fault
        never fired did not test what the scenario claims to test.
        """
        return [(fault, self._fired[index]) for index, fault in enumerate(self._faults)]

    def call(self, operation: str, **kwargs: Any) -> dict[str, Any]:
        if operation not in self.operations:
            raise ValueError(f"{self.name} has no operation {operation!r}")
        self._calls[operation] += 1
        number = self._calls[operation]
        self.ledger.clock.tick()
        firing = next(
            (
                (index, f)
                for index, f in enumerate(self._faults)
                if f.operation == operation and f.trigger.fires(number)
            ),
            None,
        )
        if firing is None:
            return self.operations[operation](**kwargs)
        index, fault = firing
        if index in self._quotas:
            try:
                self._quotas[index].admit(self.ledger.clock.now)
            except RateLimited:
                self._fired[index] += 1
                raise
            return self.operations[operation](**kwargs)
        self._fired[index] += 1
        return self._apply(fault, operation, kwargs)

    def _apply(self, fault: FaultSpec, operation: str, kwargs: dict[str, Any]) -> dict[str, Any]:
        kind = fault.kind
        if kind is FaultKind.TIMEOUT:
            raise ToolTimeout(f"{self.name}.{operation} timed out")
        if kind is FaultKind.SERVER_ERROR:
            raise ServerError(f"{self.name}.{operation} returned 500")
        if kind is FaultKind.RATE_LIMIT:
            raise RateLimited(int(fault.params.get("retry_after", 5)))
        if kind is FaultKind.PARTIAL:
            return self.partial(operation, kwargs, fault.params)
        if kind is FaultKind.SILENT_ERROR:
            return {
                "ok": False,
                "status": "error",
                "error": str(fault.params.get("error", f"{self.name}.{operation} failed upstream")),
            }
        if kind is FaultKind.LATE_COMMIT:
            delay = max(1, int(fault.params.get("delay", 2)))
            op = self.operations[operation]
            captured = dict(kwargs)

            def _deferred() -> None:
                try:
                    op(**captured)
                except ToolError:
                    return

            self.ledger.clock.schedule(delay, _deferred)
            raise ToolTimeout(f"{self.name}.{operation} timed out before commit completed")
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


def is_quota(fault: FaultSpec) -> bool:
    """Whether a fault is a fixed-window quota rather than a one-off rejection."""
    return fault.kind is FaultKind.RATE_LIMIT and "window" in fault.params

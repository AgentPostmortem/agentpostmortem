"""Simulated webhook feed: events arrive in order and can be delivered again."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agentpostmortem.sims.base import SimService
from agentpostmortem.sims.errors import NotFound


class Events(SimService):
    """An inbound event feed with at-least-once delivery.

    ``next`` hands out seeded events in order. Reading an event changes
    nothing in the outside world, so it is not recorded in the ledger. The
    ``stale`` fault is a redelivery: the call returns the event delivered
    just before, again, and the event that was due stays queued.
    """

    name = "events"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.queue: list[dict[str, Any]] = []
        self.cursor = 0
        self.operations = {"next": self._next}

    def seed(self, state: Mapping[str, Any]) -> None:
        super().seed(state)
        self.queue = [dict(event) for event in state.get("events", [])]

    def _next(self) -> dict[str, Any]:
        if self.cursor >= len(self.queue):
            raise NotFound("no events waiting")
        event = dict(self.queue[self.cursor])
        self.cursor += 1
        return event

    def stale(
        self, operation: str, response: dict[str, Any], params: Mapping[str, Any]
    ) -> dict[str, Any]:
        if self.cursor < 2:
            return response
        self.cursor -= 1
        return dict(self.queue[self.cursor - 1])

    def malformed(self, operation: str, response: dict[str, Any]) -> dict[str, Any]:
        return {key: None for key in response}

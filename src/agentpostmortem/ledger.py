"""The side-effect ledger: the single source of truth for what an agent did."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterator
from typing import Any

from agentpostmortem.domain.effects import Effect


class Clock:
    """Deterministic logical clock. Advances only when the harness says so."""

    def __init__(self) -> None:
        self.now = 0
        self._pending: list[tuple[int, int, Callable[[], None]]] = []
        self._seq = 0

    def schedule(self, delay: int, callback: Callable[[], None]) -> None:
        """Schedule ``callback`` to run once the clock reaches ``now + delay``."""
        self._seq += 1
        due = self.now + max(1, delay)
        self._pending.append((due, self._seq, callback))

    def tick(self, amount: int = 1) -> int:
        target = self.now + max(0, amount)
        while self._pending:
            next_due = min(due for due, _, _ in self._pending)
            if next_due > target:
                break
            self.now = max(self.now, next_due)
            self._drain_due()
        self.now = target
        self._drain_due()
        return self.now

    def _drain_due(self) -> None:
        while True:
            due = sorted(item for item in self._pending if item[0] <= self.now)
            if not due:
                return
            next_item = due[0]
            self._pending.remove(next_item)
            next_item[2]()

    def flush_pending(self) -> None:
        """Advance to settle any still-pending scheduled callbacks before judging."""
        while self._pending:
            next_tick = min(due for due, _, _ in self._pending)
            self.now = max(self.now, next_tick)
            self._drain_due()


class Ledger:
    def __init__(self, clock: Clock | None = None) -> None:
        self.clock = clock or Clock()
        self._effects: list[Effect] = []

    def record(
        self,
        service: str,
        operation: str,
        subject: str,
        payload: dict[str, Any] | None = None,
        *,
        idempotency_key: str | None = None,
        applied: bool = True,
    ) -> Effect:
        effect = Effect(
            seq=len(self._effects) + 1,
            service=service,
            operation=operation,
            subject=subject,
            payload=dict(payload or {}),
            idempotency_key=idempotency_key,
            applied=applied,
            tick=self.clock.now,
        )
        self._effects.append(effect)
        return effect

    def __iter__(self) -> Iterator[Effect]:
        return iter(self._effects)

    def __len__(self) -> int:
        return len(self._effects)

    def applied(self, service: str | None = None, operation: str | None = None) -> list[Effect]:
        return [
            e
            for e in self._effects
            if e.applied
            and (service is None or e.service == service)
            and (operation is None or e.operation == operation)
        ]

    def duplicates(self) -> list[tuple[tuple[str, str, str], list[Effect]]]:
        """Applied effects that happened more than once for the same subject."""
        groups: dict[tuple[str, str, str], list[Effect]] = {}
        for effect in self.applied():
            groups.setdefault((effect.service, effect.operation, effect.subject), []).append(effect)
        return [(key, effects) for key, effects in groups.items() if len(effects) > 1]

    def counts(self) -> Counter[str]:
        return Counter(f"{e.service}.{e.operation}" for e in self.applied())

    def to_list(self) -> list[dict[str, Any]]:
        return [
            {
                "seq": e.seq,
                "service": e.service,
                "operation": e.operation,
                "subject": e.subject,
                "payload": e.payload,
                "idempotency_key": e.idempotency_key,
                "applied": e.applied,
                "tick": e.tick,
            }
            for e in self._effects
        ]

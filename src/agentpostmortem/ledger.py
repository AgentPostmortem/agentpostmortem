"""The side-effect ledger: the single source of truth for what an agent did."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterator
from typing import Any

from agentpostmortem.domain.effects import Effect


class Clock:
    """Deterministic logical clock. Advances only when the harness says so."""

    def __init__(self) -> None:
        self.now = 0

    def tick(self, amount: int = 1) -> int:
        self.now += amount
        return self.now


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

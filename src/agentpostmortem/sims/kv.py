"""Simulated versioned key-value store with conditional writes."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agentpostmortem.sims.base import SimService
from agentpostmortem.sims.errors import Conflict, InvalidRequest, NotFound


class KeyValue(SimService):
    """A key-value store where every write bumps a per-key version.

    * ``put`` with ``expected_version`` is a compare-and-set: it is rejected
      with a conflict unless the key is at exactly that version. Version 0
      means "the key must not exist yet", which makes ``put`` a claim.
    * ``incr`` is an atomic counter, like ``INCRBY``. It is not idempotent and,
      like most stores, ignores idempotency keys: every call moves the counter.
    * Idempotency keys sent to ``put`` are ignored too; conditional writes are
      how this store protects against lost updates.
    """

    name = "kv"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.values: dict[str, Any] = {}
        self.versions: dict[str, int] = {}
        self.history: dict[str, list[Any]] = {}
        self.operations = {"get": self._get, "put": self._put, "incr": self._incr}

    def seed(self, state: Mapping[str, Any]) -> None:
        super().seed(state)
        older = dict(state.get("history", {}))
        for key, value in dict(state.get("entries", {})).items():
            past = list(older.get(key, []))
            self.history[key] = past
            self.values[key] = value
            self.versions[key] = len(past) + 1

    def _get(self, key: str) -> dict[str, Any]:
        if key not in self.values:
            raise NotFound(f"no key {key}")
        return {"key": key, "value": self.values[key], "version": self.versions[key]}

    def _write(self, operation: str, key: str, value: Any, extra: dict[str, Any]) -> int:
        if key in self.values:
            self.history.setdefault(key, []).append(self.values[key])
        self.values[key] = value
        version = self.versions.get(key, 0) + 1
        self.versions[key] = version
        self.ledger.record(self.name, operation, key, {**extra, "version": version})
        return version

    def _put(
        self,
        key: str,
        value: Any,
        expected_version: int | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        current = self.versions.get(key, 0)
        if expected_version is not None and expected_version != current:
            raise Conflict(f"{key} is at version {current}, not {expected_version}")
        version = self._write("put", key, value, {"value": value})
        return {"key": key, "version": version}

    def _incr(self, key: str, by: int = 1, idempotency_key: str | None = None) -> dict[str, Any]:
        current = self.values.get(key, 0)
        if not isinstance(current, int) or not isinstance(by, int):
            raise InvalidRequest(f"{key} is not an integer counter")
        value = current + by
        version = self._write("incr", key, value, {"by": by, "value": value})
        return {"key": key, "value": value, "version": version}

    def stale(
        self, operation: str, response: dict[str, Any], params: Mapping[str, Any]
    ) -> dict[str, Any]:
        """A cache that is one write behind: the previous value and version."""
        key = str(response.get("key"))
        past = self.history.get(key, [])
        if operation != "get" or not past:
            return response
        return {**response, "value": past[-1], "version": int(response["version"]) - 1}

    def malformed(self, operation: str, response: dict[str, Any]) -> dict[str, Any]:
        return {"key": response.get("key"), "value": None, "version": None}

    def lying(
        self, operation: str, response: dict[str, Any], params: Mapping[str, Any]
    ) -> dict[str, Any]:
        return {**response, "value": params.get("value", response.get("value"))}

"""Simulated workspace file store: optimistic writes, partial patches, non-idempotent appends."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any

from agentpostmortem.sims.base import SimService
from agentpostmortem.sims.errors import Conflict, InvalidRequest, NotFound


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


class Files(SimService):
    """Workspace files with mixed safety guarantees matching coding & agent-computer tools.

    * ``read`` returns content and SHA; reads change nothing in the ledger.
    * ``write`` honours ``expected_sha`` (optimistic locking) and ``idempotency_key``.
    * ``append`` ignores ``idempotency_key``, so blind retries duplicate lines.
    * ``apply_patch_batch`` applies multiple file updates atomically or reports ``status: partial``.
    """

    name = "files"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.contents: dict[str, str] = {}
        self.history: dict[str, list[str]] = {}
        self._keys: dict[str, dict[str, Any]] = {}
        self._batch_keys: dict[str, dict[str, Any]] = {}
        self.operations = {
            "read": self._read,
            "write": self._write,
            "append": self._append,
            "apply_patch_batch": self._apply_patch_batch,
        }

    def seed(self, state: Mapping[str, Any]) -> None:
        super().seed(state)
        raw_history = dict(state.get("history", {}))
        for path, content in dict(state.get("contents", {})).items():
            text = str(content)
            self.contents[str(path)] = text
            older = [str(item) for item in raw_history.get(path, [])]
            self.history[str(path)] = [*older, text]

    def _read(self, path: str) -> dict[str, Any]:
        if path not in self.contents:
            raise NotFound(f"no file {path}")
        content = self.contents[path]
        return {"path": path, "content": content, "sha": _sha(content)}

    def _write(
        self,
        path: str,
        content: str,
        expected_sha: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        if not isinstance(path, str) or not path:
            raise InvalidRequest("path must be a non-empty string")
        if not isinstance(content, str):
            raise InvalidRequest("content must be a string")
        if idempotency_key and idempotency_key in self._keys:
            self.ledger.record(
                self.name, "write", path, idempotency_key=idempotency_key, applied=False
            )
            return dict(self._keys[idempotency_key])
        current_sha = _sha(self.contents[path]) if path in self.contents else ""
        if expected_sha is not None and expected_sha != current_sha:
            raise Conflict(f"{path} has sha {current_sha or 'missing'}, not {expected_sha}")
        self.contents[path] = content
        self.history.setdefault(path, []).append(content)
        sha = _sha(content)
        self.ledger.record(
            self.name,
            "write",
            path,
            {"sha": sha, "bytes": len(content)},
            idempotency_key=idempotency_key,
        )
        result = {"path": path, "sha": sha, "status": "succeeded"}
        if idempotency_key:
            self._keys[idempotency_key] = result
        return dict(result)

    def _append(self, path: str, line: str, idempotency_key: str | None = None) -> dict[str, Any]:
        if not isinstance(path, str) or not path:
            raise InvalidRequest("path must be a non-empty string")
        if not isinstance(line, str) or not line:
            raise InvalidRequest("line must be a non-empty string")
        existing = self.contents.get(path, "")
        prefix = "" if not existing or existing.endswith("\n") else "\n"
        updated = f"{existing}{prefix}{line}\n"
        self.contents[path] = updated
        self.history.setdefault(path, []).append(updated)
        sha = _sha(updated)
        self.ledger.record(self.name, "append", path, {"line": line, "sha": sha})
        return {"path": path, "sha": sha, "lines": len(updated.splitlines())}

    def _apply_patch_batch(
        self,
        changes: list[dict[str, str]],
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        return self._patch_batch(changes, idempotency_key, set())

    def _patch_batch(
        self,
        changes: list[dict[str, str]],
        idempotency_key: str | None,
        rejected: set[str],
    ) -> dict[str, Any]:
        if not isinstance(changes, list) or not changes:
            raise InvalidRequest("changes must be a non-empty list")
        if idempotency_key and idempotency_key in self._batch_keys:
            cached = self._batch_keys[idempotency_key]
            for path in cached["paths"]:
                self.ledger.record(
                    self.name, "write", path, idempotency_key=idempotency_key, applied=False
                )
            return dict(cached["response"])
        applied: list[str] = []
        failed: list[str] = []
        for change in changes:
            if not isinstance(change, Mapping):
                raise InvalidRequest("each change must be an object with path and content")
            path = str(change.get("path", ""))
            content = change.get("content")
            if not path or not isinstance(content, str) or path in rejected:
                if path:
                    failed.append(path)
                continue
            self.contents[path] = content
            self.history.setdefault(path, []).append(content)
            sha = _sha(content)
            self.ledger.record(
                self.name,
                "write",
                path,
                {"sha": sha, "bytes": len(content)},
                idempotency_key=idempotency_key,
            )
            applied.append(path)
        response = {
            "status": "partial" if failed else "succeeded",
            "applied": len(applied),
            "failed": list(failed),
        }
        if idempotency_key:
            self._batch_keys[idempotency_key] = {"paths": applied, "response": response}
        return dict(response)

    def partial(
        self, operation: str, kwargs: dict[str, Any], params: Mapping[str, Any]
    ) -> dict[str, Any]:
        if operation != "apply_patch_batch":
            return super().partial(operation, kwargs, params)
        rejected = {str(p) for p in params.get("paths", [])}
        return self._patch_batch(kwargs["changes"], kwargs.get("idempotency_key"), rejected)

    def stale(
        self, operation: str, response: dict[str, Any], params: Mapping[str, Any]
    ) -> dict[str, Any]:
        path = str(response.get("path", ""))
        older = self.history.get(path, [])
        if len(older) >= 2:
            old_content = older[0]
            return {"path": path, "content": old_content, "sha": _sha(old_content)}
        content = str(params.get("content", response.get("content", "")))
        return {"path": path, "content": content, "sha": _sha(content)}

    def malformed(self, operation: str, response: dict[str, Any]) -> dict[str, Any]:
        return {"path": response.get("path"), "content": None, "sha": None}

    def lying(
        self, operation: str, response: dict[str, Any], params: Mapping[str, Any]
    ) -> dict[str, Any]:
        content = str(params.get("content", "wrong content"))
        return {**response, "content": content, "sha": _sha(content)}

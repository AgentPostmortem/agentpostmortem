"""Simulated model API: billed per completion, rate limited, no idempotency."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agentpostmortem.sims.base import SimService
from agentpostmortem.sims.errors import InvalidRequest


class Llm(SimService):
    """A completion endpoint that bills tokens for every accepted request.

    Model APIs generally do not deduplicate requests, so idempotency keys are
    ignored: a repeated prompt is computed and billed again. Every completion
    is recorded in the ledger with the tokens it cost. Seed ``tokens`` sets
    the cost of one completion and ``quota`` the rate limit.
    """

    name = "llm"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.tokens_per_call = 1000
        self.completions = 0
        self.operations = {"complete": self._complete}

    def seed(self, state: Mapping[str, Any]) -> None:
        super().seed(state)
        self.tokens_per_call = int(state.get("tokens", self.tokens_per_call))

    def _complete(self, prompt: str, idempotency_key: str | None = None) -> dict[str, Any]:
        if not isinstance(prompt, str) or not prompt:
            raise InvalidRequest("prompt must be a non-empty string")
        self.completions += 1
        tokens = self.tokens_per_call
        self.ledger.record(
            self.name, "complete", prompt, {"tokens": tokens, "completion": self.completions}
        )
        return {"text": f"completion {self.completions}", "tokens": tokens}

    def malformed(self, operation: str, response: dict[str, Any]) -> dict[str, Any]:
        return {"text": None, "tokens": response.get("tokens")}

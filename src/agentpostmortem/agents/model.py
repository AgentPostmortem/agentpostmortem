"""OpenAI-compatible LLM driver using AgentPostmortem's tool session."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from agentpostmortem.domain.outcome import AgentOutcome, Status
from agentpostmortem.domain.scenario import Scenario
from agentpostmortem.server.session import FINISH_TOOL, Session, SessionError
from agentpostmortem.toolbox import Toolbox

DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_API_KEY_ENV = "OPENAI_API_KEY"
DEFAULT_MAX_TURNS = 24
REQUEST_TIMEOUT_SECONDS = 60.0

CompletionFn = Callable[[dict[str, Any]], dict[str, Any]]


class ModelConfigError(RuntimeError):
    """Raised when the model endpoint cannot be configured or reached cleanly."""


@dataclass(frozen=True, slots=True)
class ModelOptions:
    model: str
    base_url: str = DEFAULT_BASE_URL
    api_key_env: str = DEFAULT_API_KEY_ENV
    temperature: float = 0.0
    max_turns: int = DEFAULT_MAX_TURNS


def openai_chat_completion(
    base_url: str, api_key: str | None, timeout: float = REQUEST_TIMEOUT_SECONDS
) -> CompletionFn:
    """Create a zero-dependency OpenAI-compatible ``/chat/completions`` caller."""
    url = base_url.rstrip("/") + "/chat/completions"

    def call(payload: dict[str, Any]) -> dict[str, Any]:
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data: dict[str, Any] = json.loads(resp.read().decode("utf-8"))
                return data
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise ModelConfigError(
                f"model endpoint returned HTTP {exc.code} from {url}: {body[:300]}"
            ) from exc
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            raise ModelConfigError(f"model request to {url} failed: {exc}") from exc

    return call


def _openai_tools(session: Session) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.input_schema,
            },
        }
        for tool in session.list_tools()
    ]


def _parse_arguments(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return parsed
    return {}


def _resolve_api_key(env_name: str, base_url: str) -> str | None:
    if not env_name or env_name.lower() == "none":
        return None
    key = os.environ.get(env_name) or os.environ.get("APM_API_KEY")
    if key:
        return key
    lower = base_url.lower()
    if any(host in lower for host in ("127.0.0.1", "localhost", "::1")):
        return None
    raise ModelConfigError(
        f"set {env_name} (or APM_API_KEY, or --base-url to a local server) to run --model"
    )


class ModelAgent:
    """An :class:`~agentpostmortem.agents.base.Agent` backed by a chat-completions LLM."""

    def __init__(
        self,
        options: ModelOptions,
        *,
        name: str | None = None,
        completion_fn: CompletionFn | None = None,
    ) -> None:
        self.options = options
        self.name = name or f"model:{options.model}"
        self.framework = "openai-compatible"
        self.framework_version = options.model
        self._completion_fn = completion_fn

    def _caller(self) -> CompletionFn:
        if self._completion_fn is not None:
            return self._completion_fn
        api_key = _resolve_api_key(self.options.api_key_env, self.options.base_url)
        return openai_chat_completion(self.options.base_url, api_key)

    def run(self, scenario: Scenario, tools: Toolbox, store: Any) -> AgentOutcome:
        session = Session(scenario, agent_name=self.name, transport="model", toolbox=tools)
        caller = self._caller()
        tool_defs = _openai_tools(session)
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": session.instructions},
            {"role": "user", "content": scenario.task},
        ]

        for _ in range(self.options.max_turns):
            payload: dict[str, Any] = {
                "model": self.options.model,
                "messages": messages,
                "tools": tool_defs,
                "temperature": self.options.temperature,
            }
            response = caller(payload)
            choices = response.get("choices")
            if not isinstance(choices, Sequence) or not choices:
                return AgentOutcome(
                    Status.FAILED,
                    "Model returned no completion choices.",
                    tuple(session._ok_steps),
                )
            first = choices[0]
            if not isinstance(first, Mapping):
                return AgentOutcome(
                    Status.FAILED,
                    "Model returned an unexpected choice payload.",
                    tuple(session._ok_steps),
                )
            raw_msg = first.get("message")
            message: Mapping[str, Any] = raw_msg if isinstance(raw_msg, Mapping) else {}
            assistant_entry: dict[str, Any] = {
                "role": "assistant",
                "content": message.get("content") or "",
            }
            raw_calls = message.get("tool_calls") or []
            if raw_calls:
                assistant_entry["tool_calls"] = list(raw_calls)
            messages.append(assistant_entry)

            if not raw_calls:
                claim = str(message.get("content") or "").strip()
                return AgentOutcome(
                    Status.FAILED,
                    claim or f"Model stopped without calling {FINISH_TOOL}.",
                    tuple(session._ok_steps),
                )

            for call in raw_calls:
                if not isinstance(call, Mapping):
                    continue
                call_id = str(call.get("id") or "call_0")
                raw_fn = call.get("function")
                fn: Mapping[str, Any] = raw_fn if isinstance(raw_fn, Mapping) else {}
                tool_name = str(fn.get("name") or "")
                try:
                    args = _parse_arguments(fn.get("arguments"))
                    result = session.call(tool_name, args)
                    output_text = result.text()
                    if result.ok and result.step is not None:
                        tools.finish_step(result.step)
                except (ValueError, SessionError) as err:
                    code = err.code if isinstance(err, SessionError) else "bad_arguments"
                    output_text = json.dumps(
                        {"error": code, "message": str(err)},
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                messages.append({"role": "tool", "tool_call_id": call_id, "content": output_text})
                if session.outcome is not None:
                    return session.outcome

        return AgentOutcome(
            Status.FAILED,
            f"Model exceeded max turns ({self.options.max_turns}) without calling {FINISH_TOOL}.",
            tuple(session._ok_steps),
        )

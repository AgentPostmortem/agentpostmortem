"""An MCP server for a :class:`Session`, built on the official MCP Python SDK.

Each service operation in the scenario's plan is one tool, plus ``wait`` and
``finish``. The server instructions carry the task and the plan. Needs the
``mcp`` extra: ``pip install 'agentpostmortem[mcp]'``.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import anyio
from mcp import types
from mcp.server import Server, ServerRequestContext
from mcp.server.stdio import stdio_server

from agentpostmortem import __version__
from agentpostmortem.server.session import Session, SessionError

SERVER_NAME = "agentpostmortem"


def build_server(session: Session, on_call: Callable[[], None] | None = None) -> Server[Any]:
    """An MCP server whose tools drive ``session``. ``on_call`` runs after every call."""

    tools = [
        types.Tool(name=t.name, description=t.description, input_schema=t.input_schema)
        for t in session.list_tools()
    ]

    async def list_tools(
        ctx: ServerRequestContext[Any], params: types.PaginatedRequestParams | None
    ) -> types.ListToolsResult:
        return types.ListToolsResult(tools=tools)

    async def call_tool(
        ctx: ServerRequestContext[Any], params: types.CallToolRequestParams
    ) -> types.CallToolResult:
        try:
            result = session.call(params.name, params.arguments or {})
        except SessionError as error:
            body = {"error": error.code, "message": error.message}
            text = json.dumps(body, sort_keys=True, separators=(",", ":"))
            return types.CallToolResult(
                content=[types.TextContent(type="text", text=text)],
                structured_content=body,
                is_error=True,
            )
        if on_call is not None:
            on_call()
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=result.text())],
            structured_content=result.body,
            is_error=not result.ok,
        )

    return Server(
        SERVER_NAME,
        version=__version__,
        title=f"AgentPostmortem {session.scenario.id}",
        instructions=session.instructions,
        on_list_tools=list_tools,
        on_call_tool=call_tool,
    )


def serve_stdio(session: Session, on_call: Callable[[], None] | None = None) -> None:
    """Serve ``session`` over stdio until the client disconnects."""
    server = build_server(session, on_call)

    async def main() -> None:
        async with stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream, server.create_initialization_options())

    anyio.run(main)

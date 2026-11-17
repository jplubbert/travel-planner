"""AssistantTools over MCP: the assistant discovers and calls the server's tools with the
official MCP client, over streamable HTTP. By default the HTTP requests go to the API's
own ASGI app in the same process (no network hop); the protocol is the same one an
external MCP client would use."""

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx2
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client

from app.ports.assistant import ToolCall, ToolResult, ToolSpec

MCP_URL = "http://localhost/mcp/"


class McpTools:
    def __init__(self, session: ClientSession) -> None:
        self._session = session

    async def list_tools(self) -> list[ToolSpec]:
        listed = await self._session.list_tools()
        return [ToolSpec(t.name, t.description or "", dict(t.input_schema))
                for t in listed.tools]

    async def call_tool(self, call: ToolCall) -> ToolResult:
        result = await self._session.call_tool(call.name, call.arguments)
        parts = [getattr(c, "text", "") for c in result.content]
        text = "\n".join(p for p in parts if p)
        if not text and result.structured_content is not None:
            text = json.dumps(result.structured_content)
        return ToolResult(call, bool(result.is_error), text)


@asynccontextmanager
async def mcp_tools(asgi_app: Any, token: str, url: str = MCP_URL) -> AsyncIterator[McpTools]:
    """An MCP session acting for the user identified by ``token``."""
    async with (
        httpx2.AsyncClient(transport=httpx2.ASGITransport(app=asgi_app),
                           headers={"Authorization": f"Bearer {token}"}, timeout=60) as http,
        streamable_http_client(url, http_client=http, terminate_on_close=False) as streams,
        ClientSession(streams[0], streams[1]) as session,
    ):
        await session.initialize()
        yield McpTools(session)

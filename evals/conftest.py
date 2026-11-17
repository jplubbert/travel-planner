"""Evaluations reuse the integration fixtures (PostgreSQL, provider mock over HTTP, app).

- evals/deterministic: no model access; part of the default `uv run pytest`.
- evals/llm: marked `llm`, excluded by default; need GEMINI_API_KEY (see evals/README.md).
"""

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

import pytest

from tests.integration.conftest import *  # noqa: F403  (fixtures: app, api, mock_control...)


@pytest.fixture
def mcp_as(app: Any) -> Callable[..., Any]:
    """An MCP client session over HTTP to the app's /mcp, acting for ``user_id`` (or with an
    explicit raw ``token``), exactly as the assistant connects."""
    from app.adapters.mcp_client import mcp_tools
    from app.mcp.identity import issue_token

    @asynccontextmanager
    async def connect(user_id: str | None = None, *, token: str | None = None,
                      ttl: float = 300) -> AsyncIterator[Any]:
        if token is None:
            assert user_id is not None
            token = issue_token(app.state.settings.mcp_token_secret, user_id,
                                datetime.now(UTC), ttl)
        async with mcp_tools(app, token) as tools:
            yield tools

    return connect

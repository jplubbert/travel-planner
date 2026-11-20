"""Secrets in Settings (Gemini key, MCP token secret) never show up in reprs, logs or
tracebacks."""

import traceback

import pytest

from app.config import Settings

KEY = "AIza-test-secret-value-1234567890"
MCP_SECRET = "prod-mcp-signing-secret-0987654321"  # module level: not a frame local


def settings(**overrides: object) -> Settings:
    return Settings(_env_file=None, **overrides)  # type: ignore[call-arg]


def test_gemini_api_key_is_masked_in_repr_and_str() -> None:
    configured = settings(gemini_api_key=KEY)
    assert KEY not in repr(configured)
    assert KEY not in str(configured)
    assert KEY not in repr(configured.gemini_api_key)
    assert KEY not in configured.model_dump_json()
    assert configured.gemini_api_key is not None
    assert configured.gemini_api_key.get_secret_value() == KEY


def test_gemini_api_key_is_masked_in_tracebacks_with_locals() -> None:
    def fails(key: object) -> None:
        raise RuntimeError("boom")

    configured = settings(gemini_api_key=KEY)
    try:
        fails(configured.gemini_api_key)
    except RuntimeError as exc:
        rendered = "".join(traceback.TracebackException.from_exception(
            exc, capture_locals=True).format())
    assert "boom" in rendered
    assert KEY not in rendered


def test_gemini_api_key_from_environment_is_masked(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", KEY)
    configured = settings()
    assert configured.gemini_api_key is not None
    assert KEY not in repr(configured)


def test_empty_gemini_api_key_counts_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert not settings(gemini_api_key="").gemini_api_key
    assert settings().gemini_api_key is None


def test_mcp_token_secret_is_masked_and_still_signs() -> None:
    from datetime import UTC, datetime

    from app.mcp.identity import issue_token, verify_token

    configured = settings(mcp_token_secret=MCP_SECRET)
    assert MCP_SECRET not in repr(configured)
    assert MCP_SECRET not in str(configured)
    assert MCP_SECRET not in configured.model_dump_json()
    now = datetime(2030, 6, 1, tzinfo=UTC)
    token = issue_token(configured.mcp_token_secret, "ana", now, 60)
    assert verify_token(configured.mcp_token_secret, token, now).user_id == "ana"


def test_mcp_token_secret_is_masked_in_tracebacks_from_token_code() -> None:
    from datetime import UTC, datetime

    from app.mcp.identity import InvalidToken, verify_token

    configured = settings(mcp_token_secret=MCP_SECRET)
    try:
        verify_token(configured.mcp_token_secret, "tpmcp1.YW5h.1.bad", datetime.now(UTC))
    except InvalidToken as exc:
        rendered = "".join(traceback.TracebackException.from_exception(
            exc, capture_locals=True).format())
    assert "signature" in rendered
    assert MCP_SECRET not in rendered


@pytest.mark.parametrize("secret,warned", [(None, True), (MCP_SECRET, False)])
async def test_startup_warns_when_the_mcp_token_secret_is_the_development_value(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch, secret: str | None,
    warned: bool,
) -> None:
    from app.api.app import create_app

    monkeypatch.delenv("MCP_TOKEN_SECRET", raising=False)
    configured = settings(**({"mcp_token_secret": secret} if secret else {}))
    with caplog.at_level("WARNING", logger="app"):
        app = create_app(configured)
    await app.state.shutdown()
    warnings = [r.getMessage() for r in caplog.records
                if r.levelname == "WARNING" and "MCP_TOKEN_SECRET" in r.getMessage()]
    assert bool(warnings) is warned
    assert all(MCP_SECRET not in w for w in warnings)

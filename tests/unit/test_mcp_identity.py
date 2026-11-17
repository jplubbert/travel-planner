"""MCP session identity: signed, expiring tokens (ADR 0006)."""

from datetime import UTC, datetime, timedelta

import pytest

from app.mcp.identity import InvalidToken, bearer, issue_token, verify_token

NOW = datetime(2030, 6, 1, 12, tzinfo=UTC)
SECRET = "test-secret"


def test_valid_token_identifies_the_user():
    token = issue_token(SECRET, "carla", NOW, 300)
    identity = verify_token(SECRET, token, NOW + timedelta(seconds=10))
    assert identity.user_id == "carla"


@pytest.mark.parametrize("mutate", [
    lambda t: t.replace("Y2FybGE", "ZGFuaQ"),  # carla -> dani, same signature
    lambda t: t[:-1] + ("0" if t[-1] != "0" else "1"),
    lambda t: "garbage",
    lambda t: t.replace("tpmcp1", "other"),
])
def test_tampered_tokens_are_rejected(mutate):
    token = issue_token(SECRET, "carla", NOW, 300)
    with pytest.raises(InvalidToken):
        verify_token(SECRET, mutate(token), NOW)


def test_other_secret_and_expiry_are_rejected():
    token = issue_token(SECRET, "carla", NOW, 300)
    with pytest.raises(InvalidToken):
        verify_token("another-secret", token, NOW)
    with pytest.raises(InvalidToken, match="expired"):
        verify_token(SECRET, token, NOW + timedelta(seconds=300))


def test_bearer_header_parsing():
    assert bearer({"authorization": "Bearer abc"}) == "abc"
    assert bearer({"authorization": "Basic abc"}) is None
    assert bearer({}) is None and bearer(None) is None

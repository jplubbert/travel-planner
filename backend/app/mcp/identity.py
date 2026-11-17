"""Identity of the acting user in an MCP session (ADR 0006).

The MCP SDK warns that request headers are client-supplied and must not be trusted as an
identity assertion. So the identity travels as a short-lived token signed with a server
secret (HMAC-SHA256), issued by the application for the authenticated user who opened the
assistant session. Neither the model (tools take no identity argument) nor an external
client without the secret can choose on whose behalf tools act.
"""

import base64
import hashlib
import hmac
from dataclasses import dataclass
from datetime import datetime, timedelta

PREFIX = "tpmcp1"


class InvalidToken(Exception):
    pass


@dataclass(frozen=True)
class McpIdentity:
    user_id: str
    expires_at: datetime


def _sign(secret: str, payload: str) -> str:
    return hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()


def issue_token(secret: str, user_id: str, now: datetime, ttl_seconds: float) -> str:
    expires = int((now + timedelta(seconds=ttl_seconds)).timestamp())
    user = base64.urlsafe_b64encode(user_id.encode()).decode().rstrip("=")
    payload = f"{PREFIX}.{user}.{expires}"
    return f"{payload}.{_sign(secret, payload)}"


def verify_token(secret: str, token: str, now: datetime) -> McpIdentity:
    try:
        prefix, user, expires, signature = token.split(".")
        if prefix != PREFIX:
            raise ValueError
        payload = f"{prefix}.{user}.{expires}"
        if not hmac.compare_digest(signature, _sign(secret, payload)):
            raise InvalidToken("signature does not match")
        user_id = base64.urlsafe_b64decode(user + "=" * (-len(user) % 4)).decode()
        expires_at = datetime.fromtimestamp(int(expires), now.tzinfo)
    except InvalidToken:
        raise
    except (ValueError, UnicodeDecodeError) as exc:
        raise InvalidToken("malformed token") from exc
    if now >= expires_at:
        raise InvalidToken("token expired")
    return McpIdentity(user_id, expires_at)


def bearer(headers: object) -> str | None:
    get = getattr(headers, "get", None)
    value = get("authorization") if get else None
    if not isinstance(value, str) or not value.lower().startswith("bearer "):
        return None
    return value[7:].strip()

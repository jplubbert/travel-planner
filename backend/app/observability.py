"""Correlation id of the request being served, readable from any layer's logs."""

from contextvars import ContextVar

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)

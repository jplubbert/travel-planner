from typing import Any


class ProviderError(Exception):
    """An error rendered with the contract's Error schema."""

    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        *,
        headers: dict[str, str] | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.headers = headers or {}
        self.details = details


def not_found(
    message: str = "No visible record exists for the supplied identifier.",
) -> ProviderError:
    return ProviderError(404, "NOT_FOUND", message)


def bad_request(message: str, details: dict[str, Any] | None = None) -> ProviderError:
    return ProviderError(400, "BAD_REQUEST", message, details=details)

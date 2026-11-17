"""Secrets in Settings never show up in reprs, logs or tracebacks."""

import traceback

import pytest

from app.config import Settings

KEY = "AIza-test-secret-value-1234567890"


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

"""docker-compose.yml gives api and worker every setting app/config.py reads (from .env),
with the Docker-specific values replacing the host ones. Checked with `docker compose
config` on a copy of the file, using .env.example as .env (the real .env is never read)."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from app.config import Settings

ROOT = Path(__file__).resolve().parents[2]
DOCKER_VALUES = {
    "DATABASE_URL": "postgresql+asyncpg://travel:travel@db:5432/travel",
    "PROVIDER_BASE_URL": "http://provider-mock:4010/v1",
    "DOCUMENT_STORAGE_DIR": "/data/documents",
}


def compose_environment(tmp_path: Path, env_file: str | None,
                        shell: dict[str, str] | None = None) -> dict[str, dict[str, str]]:
    if shutil.which("docker") is None:
        pytest.skip("docker is not installed")
    shutil.copy(ROOT / "docker-compose.yml", tmp_path)
    for context in ("backend", "mock", "frontend"):
        (tmp_path / context).mkdir()
    if env_file is not None:
        (tmp_path / ".env").write_text(env_file)
    clean = {"PATH": str(Path(shutil.which("docker") or "").parent), "HOME": str(Path.home()),
             **(shell or {})}
    result = subprocess.run(["docker", "compose", "config", "--format", "json"],
                            cwd=tmp_path, env=clean, capture_output=True, text=True)
    if result.returncode != 0 and "is not a docker command" in result.stderr:
        pytest.skip("docker compose is not available")
    assert result.returncode == 0, result.stderr
    services = json.loads(result.stdout)["services"]
    return {name: services[name].get("environment", {}) for name in ("api", "worker")}


def settings_variables() -> set[str]:
    return {name.upper() for name in Settings.model_fields}


def test_api_and_worker_receive_every_setting_from_env_with_docker_overrides(tmp_path):
    example = (ROOT / ".env.example").read_text()
    for service, env in compose_environment(tmp_path, example).items():
        assert settings_variables() - set(env) == set(), service
        for name, value in DOCKER_VALUES.items():
            assert env[name] == value, (service, name)
        assert env["MCP_ALLOWED_HOSTS"] == '["localhost","localhost:*","127.0.0.1:*","api:*"]'
        assert env["BOOKING_PENDING_POLL_SECONDS"] == "5"


def test_without_env_the_containers_still_get_the_docker_values(tmp_path):
    for env in compose_environment(tmp_path, None).values():
        for name, value in DOCKER_VALUES.items():
            assert env[name] == value


def test_a_shell_value_blanks_the_gemini_key_from_env(tmp_path):
    """scripts/e2e.sh relies on this so the e2e stack never calls the model."""
    env_file = "GEMINI_API_KEY=from-dotenv\nMCP_TOKEN_SECRET=from-dotenv\n"
    for env in compose_environment(tmp_path, env_file, {"GEMINI_API_KEY": ""}).values():
        assert env["GEMINI_API_KEY"] == ""
        assert env["MCP_TOKEN_SECRET"] == "from-dotenv"

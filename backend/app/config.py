from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Environment configuration (see .env.example)."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://travel:travel@localhost:5432/travel"
    log_level: str = "INFO"

    # External provider (assignment/openAPI.json). Only the public /v1 contract is used.
    provider_base_url: str = "http://localhost:4010/v1"
    provider_token: str = "travel-demo-token"
    # Overall deadline per provider call; nothing waits on the provider longer than this.
    provider_timeout_seconds: float = 5.0

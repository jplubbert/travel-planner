from typing import TYPE_CHECKING

from pydantic_settings import BaseSettings, SettingsConfigDict

if TYPE_CHECKING:
    from app.domain.reservations import ReconcilePolicy


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

    # Booking reconciliation (ADR 0003) and the background worker.
    booking_unknown_grace_seconds: float = 30.0
    # Must exceed provider_timeout_seconds: younger "submitted" bookings may still be in flight.
    booking_submitted_stale_seconds: float = 60.0
    booking_pending_poll_seconds: float = 5.0
    booking_retry_base_seconds: float = 2.0
    booking_retry_max_seconds: float = 60.0
    worker_poll_interval_seconds: float = 1.0
    worker_job_lease_seconds: float = 120.0
    worker_batch_size: int = 10

    # Documents (US-09/US-10, ADR 0005).
    document_storage_dir: str = "data/documents"
    document_max_attempts: int = 3
    document_sweep_interval_seconds: float = 300.0

    # Status channel (SSE): how often a stream re-checks state and access.
    events_poll_seconds: float = 1.0
    events_heartbeat_seconds: float = 15.0

    def reconcile_policy(self) -> "ReconcilePolicy":
        from app.domain.reservations import ReconcilePolicy
        return ReconcilePolicy(
            unknown_grace_seconds=self.booking_unknown_grace_seconds,
            submitted_stale_seconds=self.booking_submitted_stale_seconds,
            pending_poll_seconds=self.booking_pending_poll_seconds,
            retry_base_seconds=self.booking_retry_base_seconds,
            retry_max_seconds=self.booking_retry_max_seconds,
        )

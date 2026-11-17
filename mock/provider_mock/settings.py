import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    # Accepted bearer tokens. Each token is a distinct caller: bookings, references
    # and idempotency keys are scoped to it.
    tokens: tuple[str, ...] = ("travel-demo-token",)
    state_file: Path = Path("data/mock-state.json")
    quote_ttl_seconds: int = 900
    # How long an aggregated search waits for a supplier in "timeout" mode before
    # reporting PROVIDER_TIMEOUT for it.
    supplier_deadline_seconds: float = 2.0
    # Test-only: yield to the event loop between the idempotency/reference checks and
    # the write, so concurrency tests fail if the booking lock is removed.
    yield_in_critical_section: bool = False

    @classmethod
    def from_env(cls) -> "Settings":
        tokens = tuple(
            t.strip() for t in os.environ.get("MOCK_TOKENS", "travel-demo-token").split(",")
        )
        return cls(
            tokens=tuple(t for t in tokens if t),
            state_file=Path(os.environ.get("MOCK_STATE_FILE", "data/mock-state.json")),
            quote_ttl_seconds=int(os.environ.get("MOCK_QUOTE_TTL_SECONDS", "900")),
            supplier_deadline_seconds=float(
                os.environ.get("MOCK_SUPPLIER_DEADLINE_SECONDS", "2.0")
            ),
        )

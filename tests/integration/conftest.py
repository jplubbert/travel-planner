"""Integration tests run against a real PostgreSQL (docker compose up -d db).

A dedicated database (default ``travel_test``) is recreated once per session and migrated
with Alembic (upgrade, full downgrade, upgrade again). Each test starts with empty data
tables; seeded users remain.
"""

import asyncio
import os
import socket
import subprocess
import sys
import time
from collections.abc import AsyncIterator, Callable, Iterator
from pathlib import Path
from typing import Any

import asyncpg
import httpx
import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.api.app import create_app
from app.config import Settings

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
PROVIDER_TOKEN = "travel-demo-token"
# The mock waits this long for a supplier in "timeout" mode before reporting it failed.
MOCK_SUPPLIER_DEADLINE = 1.0
TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+asyncpg://travel:travel@localhost:5432/travel_test"
)
DATA_TABLES = "trips, trip_members, trip_changes, itinerary_items, bookings, documents, jobs"


async def _recreate_database(url: str) -> None:
    parsed = make_url(url)
    conn = await asyncpg.connect(
        user=parsed.username, password=parsed.password, host=parsed.host,
        port=parsed.port or 5432, database="postgres",
    )
    try:
        await conn.execute(f'DROP DATABASE IF EXISTS "{parsed.database}" WITH (FORCE)')
        await conn.execute(f'CREATE DATABASE "{parsed.database}"')
    finally:
        await conn.close()


def alembic_config(url: str) -> Config:
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", url)
    cfg.attributes["configure_logger"] = False
    return cfg


@pytest.fixture(scope="session")
def database_url() -> str:
    try:
        asyncio.run(_recreate_database(TEST_DATABASE_URL))
    except (OSError, asyncpg.PostgresError) as exc:
        pytest.fail(
            f"PostgreSQL not reachable for integration tests ({exc}). "
            "Start it with: docker compose up -d db", pytrace=False,
        )
    cfg = alembic_config(TEST_DATABASE_URL)
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "base")  # downgrades must work too
    command.upgrade(cfg, "head")
    return TEST_DATABASE_URL


@pytest.fixture(autouse=True)
async def clean_tables(database_url: str) -> None:
    engine = create_async_engine(database_url, poolclass=NullPool)
    async with engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {DATA_TABLES} CASCADE"))
    await engine.dispose()


@pytest.fixture(scope="session")
def mock_provider_url(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    """The provider mock as a real HTTP server (separate process), like in production."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    env = {
        **os.environ,
        "PYTHONPATH": str(ROOT / "mock"),
        "MOCK_TOKENS": PROVIDER_TOKEN,
        "MOCK_STATE_FILE": str(tmp_path_factory.mktemp("mock") / "state.json"),
        "MOCK_SUPPLIER_DEADLINE_SECONDS": str(MOCK_SUPPLIER_DEADLINE),
    }
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "provider_mock.app:create_app", "--factory",
         "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
        env=env,
    )
    url = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 15
    while True:
        try:
            if httpx.get(f"{url}/healthz", timeout=0.5).status_code == 200:
                break
        except httpx.TransportError:
            pass
        if process.poll() is not None or time.monotonic() > deadline:
            process.kill()
            pytest.fail("provider mock did not start", pytrace=False)
        time.sleep(0.1)
    yield url
    process.terminate()
    process.wait(timeout=10)


@pytest.fixture
async def mock_control(mock_provider_url: str) -> AsyncIterator[httpx.AsyncClient]:
    """Client for the mock's /_control endpoints. Only tests use it, never the app.
    The mock is reset before each test that requests it."""
    async with httpx.AsyncClient(base_url=f"{mock_provider_url}/_control") as client:
        (await client.post("/reset")).raise_for_status()
        yield client


AppFactory = Callable[..., FastAPI]


@pytest.fixture
def storage_dir(tmp_path: Path) -> Path:
    """Document storage shared by every app instance and worker of one test."""
    return tmp_path / "documents"


@pytest.fixture
async def make_app(database_url: str, mock_provider_url: str,
                   storage_dir: Path) -> AsyncIterator[AppFactory]:
    created: list[FastAPI] = []
    mcp_runs: list[tuple[asyncio.Event, asyncio.Task[None]]] = []

    async def run_mcp(application: FastAPI, ready: asyncio.Event, stop: asyncio.Event) -> None:
        # The lifespan (not run by ASGITransport) normally starts MCP request handling.
        async with application.state.mcp_server.session_manager.run():
            ready.set()
            await stop.wait()

    def factory(provider: Any = None, chat_model: Any = None, **overrides: object) -> FastAPI:
        settings = Settings(**{
            "database_url": database_url,
            "log_level": "WARNING",
            "provider_base_url": f"{mock_provider_url}/v1",
            "provider_token": PROVIDER_TOKEN,
            "provider_timeout_seconds": 3.0,
            "document_storage_dir": str(storage_dir),
            "events_poll_seconds": 0.05,
            **overrides,
        })
        application = create_app(settings, provider=provider, chat_model=chat_model)
        created.append(application)
        ready, stop = asyncio.Event(), asyncio.Event()
        mcp_runs.append((stop, asyncio.get_running_loop().create_task(
            run_mcp(application, ready, stop))))
        return application

    yield factory
    for stop, task in mcp_runs:
        stop.set()
        await task
    for application in created:
        await application.state.shutdown()


@pytest.fixture
async def app(make_app: AppFactory) -> FastAPI:
    return make_app()


class Api:
    """HTTP clients acting as the seeded test users."""

    def __init__(self, app: FastAPI) -> None:
        self.app = app
        self._clients: list[httpx.AsyncClient] = []

    def as_(self, user_id: str | None, app: FastAPI | None = None) -> httpx.AsyncClient:
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app or self.app),
            base_url="http://api",
            headers={"X-User-Id": user_id} if user_id else {},
        )
        self._clients.append(client)
        return client

    async def close(self) -> None:
        for client in self._clients:
            await client.aclose()


@pytest.fixture
async def api(app: FastAPI) -> AsyncIterator[Api]:
    instance = Api(app)
    yield instance
    await instance.close()


TRIP = {
    "name": "Iberia + Paris",
    "destinations": ["Lisbon", "Madrid", "Paris"],
    "startsOn": "2030-06-10",
    "endsOn": "2030-06-22",
    "travelers": 2,
    "budget": "4500.00",
}


@pytest.fixture
def trip_payload() -> dict[str, object]:
    return dict(TRIP)


@pytest.fixture
async def shared_trip(api, trip_payload):
    """Ana organizes a 2-traveler trip (Lisbon, Madrid, Paris; 2030-06-10..22);
    Beto edits; Carla views; Dani is not a participant."""
    ana = api.as_("ana")
    trip = (await ana.post("/trips", json=trip_payload)).json()
    for user, role in (("beto", "editor"), ("carla", "viewer")):
        r = await ana.put(f"/trips/{trip['id']}/members/{user}", json={"role": role})
        assert r.status_code == 201, r.text
    return trip


async def insert_booking(app: FastAPI, trip_id: str, item: dict, status: str,
                         accepted_price: str | None = None) -> None:
    """Bookings are created by US-07 code paths; until then tests insert them directly."""
    from datetime import UTC, datetime
    from decimal import Decimal
    from uuid import UUID, uuid4

    from app.adapters.sqlalchemy.uow import SqlUnitOfWork
    from app.domain.bookings import Booking, BookingStatus

    now = datetime.now(UTC)
    accepted = accepted_price is not None
    async with SqlUnitOfWork(app.state.sessions) as uow:
        await uow.bookings.add(Booking(
            id=uuid4(), trip_id=UUID(trip_id), itinerary_item_id=UUID(item["itemId"]),
            offer_id=item["offerId"], expected_revision=item["quote"]["revision"],
            expected_price=Decimal(item["quote"]["price"]["amount"]),
            client_reference=f"ref-{uuid4()}", idempotency_key=f"key-{uuid4()}",
            requested_by="ana", status=BookingStatus(status), created_at=now, updated_at=now,
            submitted_at=now, provider_booking_id="booking-x" if accepted else None,
            accepted_offer={"id": item["offerId"]} if accepted else None,
            accepted_price=Decimal(accepted_price) if accepted else None,
        ))
        await uow.commit()


@pytest.fixture
def add_booking(app: FastAPI) -> Callable[..., Any]:
    async def add(trip_id: str, item: dict, status: str, accepted_price: str | None = None):
        await insert_booking(app, trip_id, item, status, accepted_price)
    return add



@pytest.fixture
def migration_db(database_url: str) -> tuple[str, Callable[[str], Any]]:
    """A separate, empty database to exercise migrations step by step."""
    parsed = make_url(database_url)
    url = parsed.set(database=f"{parsed.database}_migrations").render_as_string(
        hide_password=False)
    asyncio.run(_recreate_database(url))

    async def migrate(revision: str) -> None:
        # Alembic's env.py runs its own event loop, so it needs a thread without one.
        await asyncio.to_thread(command.upgrade, alembic_config(url), revision)

    return url, migrate


@pytest.fixture
async def provider_client(mock_provider_url: str) -> AsyncIterator[Any]:
    from app.adapters.provider_http import HttpProviderClient

    client = HttpProviderClient(f"{mock_provider_url}/v1", PROVIDER_TOKEN, 3.0)
    yield client
    await client.aclose()


@pytest.fixture
def make_worker(app: FastAPI, provider_client: Any, storage_dir: Path) -> Callable[..., Any]:
    """A JobWorker on the test database, the real mock and the test's document storage.
    Defaults make every step due immediately so tests can drive it with run_once()."""
    from app.adapters.clock import SystemClock
    from app.adapters.document_html import HtmlDocumentRenderer
    from app.adapters.local_storage import LocalDocumentStorage
    from app.domain.reservations import ReconcilePolicy
    from app.workers.worker import JobWorker

    def make(name: str = "worker", provider: Any = None, storage: Any = None,
             renderer: Any = None, lease_seconds: float = 120.0, **policy: float) -> JobWorker:
        defaults = {"unknown_grace_seconds": 0.0, "submitted_stale_seconds": 0.0,
                    "pending_poll_seconds": 0.0, "retry_base_seconds": 0.0,
                    "retry_max_seconds": 0.0}
        return JobWorker(app.state.sessions, provider or provider_client,
                         storage or LocalDocumentStorage(storage_dir),
                         renderer or HtmlDocumentRenderer(), SystemClock(),
                         ReconcilePolicy(**{**defaults, **policy}),
                         lease_seconds=lease_seconds, name=name)

    return make

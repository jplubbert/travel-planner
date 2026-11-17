"""Integration tests run against a real PostgreSQL (docker compose up -d db).

A dedicated database (default ``travel_test``) is recreated once per session and migrated
with Alembic (upgrade, full downgrade, upgrade again). Each test starts with empty data
tables; seeded users remain.
"""

import asyncio
import os
from collections.abc import AsyncIterator
from pathlib import Path

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

BACKEND = Path(__file__).resolve().parents[2] / "backend"
TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+asyncpg://travel:travel@localhost:5432/travel_test"
)
DATA_TABLES = "trips, trip_members, itinerary_items, bookings, documents, jobs"


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


@pytest.fixture
async def app(database_url: str) -> AsyncIterator[FastAPI]:
    application = create_app(Settings(database_url=database_url, log_level="WARNING"))
    yield application
    await application.state.engine.dispose()


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

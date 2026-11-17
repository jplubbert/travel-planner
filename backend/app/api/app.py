import logging
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from sqlalchemy import text

from app.adapters.clock import SystemClock
from app.adapters.provider_http import HttpProviderClient
from app.adapters.sqlalchemy.uow import make_engine, session_factory
from app.api.errors import error_response, install_error_handlers
from app.api.routes import bookings, itinerary, search, trips
from app.config import Settings
from app.observability import request_id_var
from app.ports.clock import Clock
from app.ports.provider import ProviderClient

logger = logging.getLogger("app")


def create_app(
    settings: Settings | None = None, clock: Clock | None = None,
    provider: ProviderClient | None = None,
) -> FastAPI:
    settings = settings or Settings()
    logging.basicConfig(level=settings.log_level,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    engine = make_engine(settings.database_url)
    http_provider = None
    if provider is None:
        provider = http_provider = HttpProviderClient(
            settings.provider_base_url, settings.provider_token,
            settings.provider_timeout_seconds)

    async def shutdown() -> None:
        if http_provider is not None:
            await http_provider.aclose()
        await engine.dispose()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        yield
        await shutdown()

    app = FastAPI(title="Travel Planner API", version="0.1.0", lifespan=lifespan)
    app.state.engine = engine
    app.state.sessions = session_factory(engine)
    app.state.clock = clock or SystemClock()
    app.state.provider = provider
    app.state.shutdown = shutdown  # for callers that do not run the lifespan (tests)

    @app.middleware("http")
    async def request_context(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request.state.request_id = request.headers.get("X-Request-Id") or uuid.uuid4().hex
        request_id_var.set(request.state.request_id)
        try:
            response = await call_next(request)
        except Exception:
            logger.exception("unhandled error request_id=%s", request.state.request_id)
            response = error_response(request, 500, "INTERNAL_ERROR",
                                      "Unexpected error; quote the requestId when reporting it.")
        response.headers["X-Request-Id"] = request.state.request_id
        logger.info("%s %s -> %s request_id=%s user=%s", request.method, request.url.path,
                    response.status_code, request.state.request_id,
                    request.headers.get("X-User-Id", "-"))
        return response

    install_error_handlers(app, itinerary.conflict_details)
    app.include_router(trips.router)
    app.include_router(search.router)
    app.include_router(itinerary.router)
    app.include_router(bookings.router)

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz", include_in_schema=False)
    async def readyz(request: Request) -> Response:
        try:
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
        except Exception:
            return error_response(request, 503, "NOT_READY", "Database is not reachable.")
        return Response(status_code=204)

    return app

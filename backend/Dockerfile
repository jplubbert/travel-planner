# Build context: repository root (needs pyproject.toml and uv.lock).
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.13.4 /uv /bin/uv
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv UV_COMPILE_BYTECODE=1 \
    PATH=/opt/venv/bin:$PATH PYTHONPATH=/srv/backend

WORKDIR /srv
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY backend ./backend

RUN useradd --system --uid 10001 app
USER app
EXPOSE 8000

# Migrations run before serving; uvicorn handles SIGTERM with a graceful shutdown.
CMD ["sh", "-c", "alembic -c backend/alembic.ini upgrade head && exec uvicorn app.api.app:create_app --factory --host 0.0.0.0 --port 8000"]

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MOCK_STATE_FILE=/data/mock-state.json

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY provider_mock ./provider_mock

RUN useradd --system --uid 10001 mock && mkdir -p /data && chown mock /data
USER mock
VOLUME ["/data"]
EXPOSE 4010

# Single worker: booking idempotency is serialized in-process (see ADR 0002).
CMD ["uvicorn", "provider_mock.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "4010", "--workers", "1"]

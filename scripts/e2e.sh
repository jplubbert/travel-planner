#!/usr/bin/env bash
# Full end-to-end run from scratch: builds and starts a separate Compose project (its own
# volumes and ports, so a local stack and its data are untouched), runs the Playwright tests
# against it through the frontend, then removes it. The assistant runs without a key here
# (GEMINI_API_KEY empty), so no model calls are made.
set -euo pipefail
cd "$(dirname "$0")/.."

export COMPOSE_PROJECT_NAME=tp-e2e POSTGRES_PORT=55432 API_PORT=18000 MOCK_PORT=14010 \
  FRONTEND_PORT=15173 GEMINI_API_KEY=
keep="${KEEP:-0}"  # KEEP=1 leaves the stack running after the tests

docker compose down -v --remove-orphans >/dev/null 2>&1 || true
trap '[ "$keep" = 1 ] || docker compose down -v --remove-orphans >/dev/null 2>&1' EXIT
docker compose up -d --build --wait

cd frontend
[ -d node_modules ] || npm ci
npx playwright install chromium >/dev/null
E2E_BASE_URL=http://localhost:15173 E2E_MOCK_URL=http://localhost:14010 npx playwright test

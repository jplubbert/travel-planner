import { defineConfig } from "@playwright/test";

// End-to-end tests against a running Docker Compose stack (see frontend/README.md).
// E2E_BASE_URL: the frontend (nginx); E2E_MOCK_URL: the provider mock, whose /_control
// endpoints only tests and demos use.
export default defineConfig({
  testDir: "e2e",
  timeout: 60_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1, // the mock's failure scenarios are global
  reporter: [["list"]],
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost:5173",
    trace: "retain-on-failure",
  },
});

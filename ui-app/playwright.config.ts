import { defineConfig } from "@playwright/test";

/**
 * Playwright config for ui-app/e2e/.
 *
 * The Phase-2 UI-tester agent runs against a live environment — it brings up
 * its own backend + frontend before exercising these specs. We keep the
 * config minimal (no webServer) so it can boot the stack however it likes.
 *
 * For local dev: in one shell `uvicorn api.main:app --port 8000`, in another
 * `cd ui-app && npm run dev`, then `npm run test:e2e`.
 */
export default defineConfig({
  testDir: "./e2e",
  timeout: 60_000,
  expect: { timeout: 10_000 },
  use: {
    baseURL: process.env.PW_BASE_URL ?? "http://localhost:5173",
    trace: "retain-on-failure",
  },
  reporter: [["list"]],
});

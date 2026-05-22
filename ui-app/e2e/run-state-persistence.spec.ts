/**
 * Slice 20: Run-state persistence via localStorage.
 *
 * Covers:
 * - run_id is saved to localStorage after generation starts
 * - succeeded state saved on completion
 * - On page refresh with a succeeded run in localStorage, download banner appears
 * - On page refresh with an in-progress run in localStorage, SSE is reconnected
 * - Dismiss button clears localStorage and hides the banner
 * - Starting a new run replaces the localStorage entry
 */

import { expect, test } from "@playwright/test";

const FAKE_RUN_ID = "aabbccdd11223344";
const NEW_RUN_ID = "11223344aabbccdd";
const STORAGE_KEY = "ef_run";
const SAMPLE_PASTE = "A witness saw the suspect leave the building at midnight.";

const SUCCESS_STREAM = [
  `event: intake\ndata: ${JSON.stringify({ stage: "intake", status: "started", detail: {} })}\n\n`,
  `event: intake\ndata: ${JSON.stringify({ stage: "intake", status: "complete", detail: { chars: 57 } })}\n\n`,
  `event: done\ndata: ${JSON.stringify({ stage: "done", status: "complete", detail: {} })}\n\n`,
  `event: end\ndata: ${JSON.stringify({ failed: false, failure_reason: null })}\n\n`,
].join("");

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

async function setStoredRun(
  page: import("@playwright/test").Page,
  run: { run_id: string; succeeded: boolean; failed: boolean },
) {
  await page.evaluate(
    ({ key, value }) => localStorage.setItem(key, JSON.stringify(value)),
    { key: STORAGE_KEY, value: run },
  );
}

async function getStoredRun(page: import("@playwright/test").Page) {
  return page.evaluate((key) => {
    const raw = localStorage.getItem(key);
    return raw ? (JSON.parse(raw) as { run_id: string; succeeded: boolean; failed: boolean }) : null;
  }, STORAGE_KEY);
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

test("run_id is saved to localStorage after generation starts", async ({ page }) => {
  await page.route("/api/runs", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ run_id: FAKE_RUN_ID }),
    });
  });
  await page.route(`/api/runs/${FAKE_RUN_ID}/events`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "text/event-stream",
      headers: { "Cache-Control": "no-cache" },
      body: SUCCESS_STREAM,
    });
  });

  await page.goto("/");
  await page.getByTestId("source-paste").fill(SAMPLE_PASTE);
  await page.getByTestId("attestation").check();
  await page.getByTestId("generate").click();

  // Wait for the run to be in-progress (run_id received)
  await expect(page.getByTestId("download")).toBeVisible({ timeout: 10_000 });

  const stored = await getStoredRun(page);
  expect(stored).not.toBeNull();
  expect(stored!.run_id).toBe(FAKE_RUN_ID);
  expect(stored!.succeeded).toBe(true);
});

test("on refresh with succeeded run in localStorage, resume banner with download link appears", async ({
  page,
}) => {
  await page.goto("/");
  await setStoredRun(page, { run_id: FAKE_RUN_ID, succeeded: true, failed: false });

  await page.reload();

  const banner = page.getByTestId("resume-banner");
  await expect(banner).toBeVisible({ timeout: 5_000 });

  const dl = page.getByTestId("resume-download");
  await expect(dl).toBeVisible();
  const href = await dl.getAttribute("href");
  expect(href).toBe(`/api/runs/${FAKE_RUN_ID}/zip`);
});

test("on refresh with in-progress run in localStorage, reconnects to SSE stream", async ({
  page,
}) => {
  await page.route(`/api/runs/${FAKE_RUN_ID}/events`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "text/event-stream",
      headers: { "Cache-Control": "no-cache" },
      body: SUCCESS_STREAM,
    });
  });

  await page.goto("/");
  await setStoredRun(page, { run_id: FAKE_RUN_ID, succeeded: false, failed: false });

  await page.reload();

  // App reconnects to SSE and eventually shows done + download
  await expect(page.getByTestId("download")).toBeVisible({ timeout: 15_000 });
  // Resume banner must NOT appear (reconnect flow shows progress inline)
  await expect(page.getByTestId("resume-banner")).not.toBeVisible();
});

test("dismiss button clears localStorage and hides the resume banner", async ({ page }) => {
  await page.goto("/");
  await setStoredRun(page, { run_id: FAKE_RUN_ID, succeeded: true, failed: false });

  await page.reload();

  await expect(page.getByTestId("resume-banner")).toBeVisible({ timeout: 5_000 });

  await page.getByTestId("resume-dismiss").click();

  await expect(page.getByTestId("resume-banner")).not.toBeVisible();

  const stored = await page.evaluate((key) => localStorage.getItem(key), STORAGE_KEY);
  expect(stored).toBeNull();
});

test("starting a new run replaces the previous run in localStorage", async ({ page }) => {
  await page.route("/api/runs", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ run_id: NEW_RUN_ID }),
    });
  });
  await page.route(`/api/runs/${NEW_RUN_ID}/events`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "text/event-stream",
      headers: { "Cache-Control": "no-cache" },
      body: SUCCESS_STREAM,
    });
  });

  await page.goto("/");
  await setStoredRun(page, { run_id: FAKE_RUN_ID, succeeded: true, failed: false });
  await page.reload();

  // Banner for old run is visible; now start a new run
  await expect(page.getByTestId("resume-banner")).toBeVisible({ timeout: 5_000 });
  await page.getByTestId("source-paste").fill(SAMPLE_PASTE);
  await page.getByTestId("attestation").check();
  await page.getByTestId("generate").click();

  // Wait for the new run to complete
  await expect(page.getByTestId("download")).toBeVisible({ timeout: 10_000 });

  // localStorage must now point at the new run
  const stored = await getStoredRun(page);
  expect(stored!.run_id).toBe(NEW_RUN_ID);
  expect(stored!.succeeded).toBe(true);
});

test("failed run in localStorage is cleared silently on next page load", async ({ page }) => {
  await page.goto("/");
  await setStoredRun(page, { run_id: FAKE_RUN_ID, succeeded: false, failed: true });

  await page.reload();

  // Neither banner nor SSE reconnect should happen
  await expect(page.getByTestId("resume-banner")).not.toBeVisible();

  // localStorage entry should be gone
  const stored = await page.evaluate((key) => localStorage.getItem(key), STORAGE_KEY);
  expect(stored).toBeNull();
});

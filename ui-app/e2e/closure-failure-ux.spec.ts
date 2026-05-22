import { expect, test } from "@playwright/test";

const SAMPLE_PASTE = "A witness saw the suspect leave the building at midnight.";

const FAKE_RUN_ID = "aabbccdd11223344";

const CLOSURE_FAILED_STREAM = [
  `event: intake\ndata: ${JSON.stringify({ stage: "intake", status: "started", detail: { run_id: FAKE_RUN_ID } })}\n\n`,
  `event: intake\ndata: ${JSON.stringify({ stage: "intake", status: "complete", detail: { chars: 57 } })}\n\n`,
  `event: extract\ndata: ${JSON.stringify({ stage: "extract", status: "started", detail: {} })}\n\n`,
  `event: extract\ndata: ${JSON.stringify({ stage: "extract", status: "complete", detail: { propositions: 2 } })}\n\n`,
  `event: close\ndata: ${JSON.stringify({
    stage: "close",
    status: "failed",
    detail: {
      gaps: [
        { proposition_id: "P1", required: 3, observed: 1, distinct_owners: ["alice"] },
        { proposition_id: "P2", required: 3, observed: 0, distinct_owners: [] },
      ],
      red_herring_gaps: [],
      dominance_gaps: [],
    },
  })}\n\n`,
  `event: end\ndata: ${JSON.stringify({ failed: true, failure_reason: null })}\n\n`,
].join("");

test.beforeEach(async ({ page }) => {
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
      body: CLOSURE_FAILED_STREAM,
    });
  });
});

test("closure failure shows inline error detail", async ({ page }) => {
  await page.goto("/");
  await page.getByTestId("source-paste").fill(SAMPLE_PASTE);
  await page.getByTestId("attestation").check();
  await page.getByTestId("generate").click();

  // The failed stage row must appear.
  await expect(page.getByTestId("stage-close-failed")).toBeVisible({ timeout: 10_000 });

  // Failure detail must be rendered (the proposition gaps).
  const detail = page.getByTestId("failure-detail");
  await expect(detail).toBeVisible();
  await expect(detail).toContainText("P1");
  await expect(detail).toContainText("P2");
});

test("closure failure shows retry button and hides download", async ({ page }) => {
  await page.goto("/");
  await page.getByTestId("source-paste").fill(SAMPLE_PASTE);
  await page.getByTestId("attestation").check();
  await page.getByTestId("generate").click();

  await expect(page.getByTestId("stage-close-failed")).toBeVisible({ timeout: 10_000 });

  // Retry button must appear in the recovery panel.
  await expect(page.getByTestId("failure-recovery")).toBeVisible();
  await expect(page.getByTestId("retry")).toBeVisible();
  await expect(page.getByTestId("retry")).toBeEnabled();

  // Download link must not appear (no corpus was produced).
  await expect(page.getByTestId("download")).not.toBeVisible();
});

test("retry button re-submits the run", async ({ page }) => {
  let callCount = 0;
  await page.route("/api/runs", async (route) => {
    callCount++;
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ run_id: FAKE_RUN_ID }),
    });
  });

  await page.goto("/");
  await page.getByTestId("source-paste").fill(SAMPLE_PASTE);
  await page.getByTestId("attestation").check();
  await page.getByTestId("generate").click();

  await expect(page.getByTestId("retry")).toBeVisible({ timeout: 10_000 });

  await page.getByTestId("retry").click();

  // A second POST /api/runs must have been issued.
  await expect(async () => {
    expect(callCount).toBeGreaterThanOrEqual(2);
  }).toPass({ timeout: 5_000 });
});

test("non-close stage failure shows generic error detail", async ({ page }) => {
  const intakeFailStream = [
    `event: intake\ndata: ${JSON.stringify({ stage: "intake", status: "started", detail: { run_id: FAKE_RUN_ID } })}\n\n`,
    `event: intake\ndata: ${JSON.stringify({ stage: "intake", status: "failed", detail: { error: "source text is empty" } })}\n\n`,
    `event: end\ndata: ${JSON.stringify({ failed: true, failure_reason: null })}\n\n`,
  ].join("");

  await page.route(`/api/runs/${FAKE_RUN_ID}/events`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "text/event-stream",
      headers: { "Cache-Control": "no-cache" },
      body: intakeFailStream,
    });
  });

  await page.goto("/");
  await page.getByTestId("source-paste").fill(SAMPLE_PASTE);
  await page.getByTestId("attestation").check();
  await page.getByTestId("generate").click();

  await expect(page.getByTestId("stage-intake-failed")).toBeVisible({ timeout: 10_000 });

  const detail = page.getByTestId("failure-detail");
  await expect(detail).toBeVisible();
  await expect(detail).toContainText("source text is empty");
  await expect(page.getByTestId("retry")).toBeVisible();
});

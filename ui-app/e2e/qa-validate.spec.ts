/**
 * QA validation for Slice 1 — paste → email-only forensic corpus zip.
 *
 * Exercises every clickable / interactive feature the slice introduces:
 *   1. Textarea (type, clear)
 *   2. Attestation checkbox (check, uncheck)
 *   3. Generate button (disabled guard, click, in-flight lock)
 *   4. SSE progress stages rendered in real time
 *   5. Download link (click triggers download, href format correct)
 *   6. Generate a second time after a completed run (state resets)
 */

import { expect, test } from "@playwright/test";

// Public-domain fixture — same corpus used by the Python smoke tests.
const SOURCE = `\
The Adventure of the Speckled Band (excerpt) — Sir Arthur Conan Doyle, 1892. Public domain.

On glancing over my notes of the seventy odd cases in which I have during the last eight years
studied the methods of my friend Sherlock Holmes, I find many tragic, some comic, a large number
merely strange, but none commonplace.

A young lady, dressed in black and heavily veiled, who had been sitting in the window, rose as we
entered. Holmes greeted her with the easy courtesy for which he was remarkable.

She told us of strange whistles in the night, of metallic clangs, and of her sister's dying words.
`.trim();

// ── helpers ──────────────────────────────────────────────────────────────────

async function fillAndAttest(page: import("@playwright/test").Page) {
  await page.getByTestId("source-paste").fill(SOURCE);
  await page.getByTestId("attestation").check();
}

// ── tests ─────────────────────────────────────────────────────────────────────

test.describe("Generate button — disabled-state guard", () => {
  test("disabled when page first loads (no paste, no attestation)", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByTestId("generate")).toBeDisabled();
  });

  test("stays disabled with paste but no attestation", async ({ page }) => {
    await page.goto("/");
    await page.getByTestId("source-paste").fill(SOURCE);
    await expect(page.getByTestId("generate")).toBeDisabled();
  });

  test("stays disabled with attestation but empty paste", async ({ page }) => {
    await page.goto("/");
    await page.getByTestId("attestation").check();
    await expect(page.getByTestId("generate")).toBeDisabled();
  });

  test("becomes enabled once paste AND attestation are both set", async ({ page }) => {
    await page.goto("/");
    await fillAndAttest(page);
    await expect(page.getByTestId("generate")).toBeEnabled();
  });

  test("goes disabled again when attestation is unchecked", async ({ page }) => {
    await page.goto("/");
    await fillAndAttest(page);
    await expect(page.getByTestId("generate")).toBeEnabled();

    await page.getByTestId("attestation").uncheck();
    await expect(page.getByTestId("generate")).toBeDisabled();
  });

  test("goes disabled again when paste is cleared", async ({ page }) => {
    await page.goto("/");
    await fillAndAttest(page);
    await expect(page.getByTestId("generate")).toBeEnabled();

    await page.getByTestId("source-paste").fill("");
    await expect(page.getByTestId("generate")).toBeDisabled();
  });
});

test.describe("Full pipeline run — SSE progress and download", () => {
  test("progress stages appear and no stage fails", async ({ page }) => {
    await page.goto("/");
    await fillAndAttest(page);
    await page.getByTestId("generate").click();

    // Progress container should appear promptly after clicking Generate.
    await expect(page.getByTestId("progress")).toBeVisible({ timeout: 5_000 });

    // Wait for the pipeline to finish (done:complete stage).
    await expect(page.getByTestId("stage-done-complete")).toBeVisible({ timeout: 30_000 });

    // No stage should have failed.
    await expect(page.locator(".stage.failed")).toHaveCount(0);
  });

  test("generate button re-enables with correct label after pipeline completes", async ({ page }) => {
    await page.goto("/");
    await fillAndAttest(page);

    // Button starts as "Generate" and enabled.
    await expect(page.getByTestId("generate")).toHaveText("Generate");
    await expect(page.getByTestId("generate")).toBeEnabled();

    await page.getByTestId("generate").click();

    // Wait for pipeline to finish.
    await expect(page.getByTestId("stage-done-complete")).toBeVisible({ timeout: 30_000 });

    // Button must return to "Generate" (not "Generating…") and be clickable again.
    await expect(page.getByTestId("generate")).toHaveText("Generate");
    await expect(page.getByTestId("generate")).toBeEnabled();
  });

  test("textarea and checkbox are locked while pipeline is running", async ({ page }) => {
    await page.goto("/");
    await fillAndAttest(page);

    // Gate the SSE stream so the submitting state (inputs disabled) persists
    // long enough for the assertions to observe it.  The fixture pipeline is
    // otherwise too fast — run_id is set and streamDone flips back to true
    // before a polling assertion can catch the transient disabled window.
    let releaseSse!: () => void;
    const sseGate = new Promise<void>(r => { releaseSse = r; });
    await page.route(/\/api\/runs\/[a-f0-9]+\/events/, async (route) => {
      await sseGate;
      await route.continue();
    });

    await page.getByTestId("generate").click();

    // POST /api/runs has resolved → run_id set → submitting=true → inputs disabled.
    // SSE is held open, so streamDone stays false and submitting stays true.
    await expect(page.locator("#paste")).toBeDisabled({ timeout: 5_000 });
    await expect(page.locator("#attest")).toBeDisabled({ timeout: 5_000 });

    // Release the gate so the pipeline can complete.
    releaseSse();
    await expect(page.getByTestId("stage-done-complete")).toBeVisible({ timeout: 30_000 });
  });

  test("download link appears after success and has the expected href shape", async ({ page }) => {
    await page.goto("/");
    await fillAndAttest(page);
    await page.getByTestId("generate").click();
    await expect(page.getByTestId("stage-done-complete")).toBeVisible({ timeout: 30_000 });

    const download = page.getByTestId("download");
    await expect(download).toBeVisible();

    const href = await download.getAttribute("href");
    expect(href).toMatch(/^\/api\/runs\/[0-9a-f]+\/zip$/);

    const dl = await download.getAttribute("download");
    expect(dl).toBeTruthy();
  });

  test("clicking the download link delivers a zip file", async ({ page }) => {
    await page.goto("/");
    await fillAndAttest(page);
    await page.getByTestId("generate").click();
    await expect(page.getByTestId("stage-done-complete")).toBeVisible({ timeout: 30_000 });

    const [download] = await Promise.all([
      page.waitForEvent("download"),
      page.getByTestId("download").click(),
    ]);

    // Playwright captured the download — filename should match the run id.
    expect(download.suggestedFilename()).toMatch(/^evidence-factory-[0-9a-f]+\.zip$/);

    // The downloaded bytes must begin with the PK magic number (zip format).
    const stream = await download.createReadStream();
    const header = await new Promise<Buffer>((resolve) => {
      const chunks: Buffer[] = [];
      stream.on("data", (c: Buffer) => { chunks.push(c); if (Buffer.concat(chunks).length >= 4) stream.destroy(); });
      stream.on("close", () => resolve(Buffer.concat(chunks)));
      stream.on("error", () => resolve(Buffer.alloc(0)));
    });
    expect(header.slice(0, 2).toString("hex")).toBe("504b"); // PK
  });
});

test.describe("Generate again — state resets cleanly", () => {
  test("a second run clears the previous progress and produces a new run id", async ({ page }) => {
    await page.goto("/");
    await fillAndAttest(page);

    // First run — unthrottled.
    await page.getByTestId("generate").click();
    await expect(page.getByTestId("stage-done-complete")).toBeVisible({ timeout: 30_000 });

    const firstHref = await page.getByTestId("download").getAttribute("href");

    // Gate the second run's SSE so the download-link-gone window is wide
    // enough to observe.  onGenerate() clears events[] synchronously
    // (succeeded=false → download hidden) before the POST resolves, but the
    // fixture pipeline is so fast that without a gate the download link
    // comes back before Playwright's polling catches the absence.
    let releaseSse!: () => void;
    const sseGate = new Promise<void>(r => { releaseSse = r; });
    await page.route(/\/api\/runs\/[a-f0-9]+\/events/, async (route) => {
      await sseGate;
      await route.continue();
    });

    // Second run — click Generate again (inputs still filled, attestation still checked).
    await page.getByTestId("generate").click();

    // onGenerate() synchronously clears events[] → succeeded=false → download link hidden.
    await expect(page.getByTestId("download")).not.toBeVisible({ timeout: 5_000 });

    // Release the gate so the second pipeline can complete.
    releaseSse();
    await expect(page.getByTestId("stage-done-complete")).toBeVisible({ timeout: 30_000 });

    const secondHref = await page.getByTestId("download").getAttribute("href");

    // The two run ids must be different (acceptance criterion: each run produces a fresh corpus).
    expect(firstHref).not.toBe(secondHref);
  });
});

import { expect, test } from "@playwright/test";

const SOURCE = `\
The Adventure of the Speckled Band (excerpt) — Sir Arthur Conan Doyle, 1892. Public domain.

A young lady, dressed in black and heavily veiled, sat in the window and rose as we entered.
Holmes greeted her with the easy courtesy for which he was remarkable.
She told us of strange whistles in the night, of metallic clangs, and of her sister's dying words.
`.trim();

test.describe("Activity log panel", () => {
  test("panel is collapsed by default and badge is absent on load", async ({ page }) => {
    await page.goto("/");

    await expect(page.getByTestId("activity-log-toggle")).toBeVisible();
    await expect(page.getByTestId("activity-log-panel")).not.toBeVisible();
    await expect(page.getByTestId("activity-log-badge")).not.toBeVisible();
  });

  test("toggle opens and closes the panel", async ({ page }) => {
    await page.goto("/");

    await page.getByTestId("activity-log-toggle").click();
    await expect(page.getByTestId("activity-log-panel")).toBeVisible();
    await expect(page.getByTestId("activity-log-empty")).toBeVisible();

    await page.getByTestId("activity-log-toggle").click();
    await expect(page.getByTestId("activity-log-panel")).not.toBeVisible();
  });

  test("log entries appear after a run and badge count increments", async ({ page }) => {
    await page.goto("/");

    await page.getByTestId("source-paste").fill(SOURCE);
    await page.getByTestId("attestation").check();
    await page.getByTestId("generate").click();

    await expect(page.getByTestId("stage-done-complete")).toBeVisible({ timeout: 30_000 });

    const badge = page.getByTestId("activity-log-badge");
    await expect(badge).toBeVisible();
    const countText = await badge.textContent();
    expect(Number(countText)).toBeGreaterThan(0);

    await page.getByTestId("activity-log-toggle").click();
    await expect(page.getByTestId("activity-log-panel")).toBeVisible();

    const entries = page.getByTestId("activity-log-entry");
    const entryCount = await entries.count();
    expect(entryCount).toBeGreaterThan(0);

    // Most recent entry is first — "Run started" or a stage entry
    await expect(entries.first()).toBeVisible();
  });

  test("clear log button removes all entries and hides badge", async ({ page }) => {
    await page.goto("/");

    await page.getByTestId("source-paste").fill(SOURCE);
    await page.getByTestId("attestation").check();
    await page.getByTestId("generate").click();

    await expect(page.getByTestId("stage-done-complete")).toBeVisible({ timeout: 30_000 });

    await page.getByTestId("activity-log-toggle").click();
    await expect(page.getByTestId("activity-log-entry").first()).toBeVisible();

    await page.getByTestId("activity-log-clear").click();

    await expect(page.getByTestId("activity-log-entry")).toHaveCount(0);
    await expect(page.getByTestId("activity-log-empty")).toBeVisible();
    await expect(page.getByTestId("activity-log-badge")).not.toBeVisible();
  });

  test("close button collapses panel without clearing entries", async ({ page }) => {
    await page.goto("/");

    await page.getByTestId("source-paste").fill(SOURCE);
    await page.getByTestId("attestation").check();
    await page.getByTestId("generate").click();

    await expect(page.getByTestId("stage-done-complete")).toBeVisible({ timeout: 30_000 });

    await page.getByTestId("activity-log-toggle").click();
    await expect(page.getByTestId("activity-log-panel")).toBeVisible();
    const entriesBefore = await page.getByTestId("activity-log-entry").count();
    expect(entriesBefore).toBeGreaterThan(0);

    // Close via ✕ button
    await page.getByTestId("activity-log-close").click();
    await expect(page.getByTestId("activity-log-panel")).not.toBeVisible();

    // Badge still shows the same count — entries not cleared
    const badgeText = await page.getByTestId("activity-log-badge").textContent();
    expect(Number(badgeText)).toBe(entriesBefore);
  });

  test("download zip click logs an info entry", async ({ page }) => {
    await page.goto("/");

    await page.getByTestId("source-paste").fill(SOURCE);
    await page.getByTestId("attestation").check();
    await page.getByTestId("generate").click();

    await expect(page.getByTestId("stage-done-complete")).toBeVisible({ timeout: 30_000 });

    const badgeBefore = Number(await page.getByTestId("activity-log-badge").textContent());

    // Intercept the download so the browser doesn't navigate away
    const [download] = await Promise.all([
      page.waitForEvent("download"),
      page.getByTestId("download").click(),
    ]);
    await download.cancel();

    // Badge count should have gone up by one
    const badgeAfter = Number(await page.getByTestId("activity-log-badge").textContent());
    expect(badgeAfter).toBe(badgeBefore + 1);

    // Open panel and confirm the most recent entry mentions "downloaded"
    await page.getByTestId("activity-log-toggle").click();
    await expect(page.getByTestId("activity-log-panel")).toBeVisible();
    await expect(page.getByTestId("activity-log-entry").first()).toContainText("downloaded");
  });

  test("log survives a run reset — second run appends entries", async ({ page }) => {
    await page.goto("/");

    await page.getByTestId("source-paste").fill(SOURCE);
    await page.getByTestId("attestation").check();

    // First run
    await page.getByTestId("generate").click();
    await expect(page.getByTestId("stage-done-complete")).toBeVisible({ timeout: 30_000 });

    const firstCountText = await page.getByTestId("activity-log-badge").textContent();
    const firstCount = Number(firstCountText);
    expect(firstCount).toBeGreaterThan(0);

    // Second run — events are appended, not replaced
    await page.getByTestId("generate").click();
    await expect(page.getByTestId("stage-done-complete")).toBeVisible({ timeout: 30_000 });

    const secondCountText = await page.getByTestId("activity-log-badge").textContent();
    const secondCount = Number(secondCountText);
    expect(secondCount).toBeGreaterThan(firstCount);
  });
});

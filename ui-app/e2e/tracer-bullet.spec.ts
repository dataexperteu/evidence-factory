import { expect, test } from "@playwright/test";

const SAMPLE_PASTE = `
A young lady, dressed in black and heavily veiled, sat in the window and rose as we entered.
Holmes greeted her with the easy courtesy for which he was remarkable.
She told us of strange whistles in the night, of metallic clangs, and of her sister's dying words.
`.trim();

test("paste → attest → generate → progress stream → download link", async ({ page }) => {
  await page.goto("/");

  await expect(page.getByRole("heading", { name: /evidence factory/i })).toBeVisible();

  const paste = page.getByTestId("source-paste");
  const attest = page.getByTestId("attestation");
  const generate = page.getByTestId("generate");

  // Generate is disabled until paste + attestation are both filled in.
  await expect(generate).toBeDisabled();

  await paste.fill(SAMPLE_PASTE);
  await expect(generate).toBeDisabled(); // still no attestation

  await attest.check();
  await expect(generate).toBeEnabled();

  await generate.click();

  // The pipeline streams through each stage; we wait for `done:complete`.
  const done = page.getByTestId("stage-done-complete");
  await expect(done).toBeVisible({ timeout: 30_000 });

  // No stage went `failed`.
  await expect(page.locator(".stage.failed")).toHaveCount(0);

  // Download link appears and points at the run's zip endpoint.
  const download = page.getByTestId("download");
  await expect(download).toBeVisible();
  const href = await download.getAttribute("href");
  expect(href).toMatch(/^\/api\/runs\/[a-f0-9]+\/zip$/);
});

test("missing attestation keeps generate disabled", async ({ page }) => {
  await page.goto("/");
  await page.getByTestId("source-paste").fill(SAMPLE_PASTE);
  await expect(page.getByTestId("generate")).toBeDisabled();
});

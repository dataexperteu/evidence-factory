import { expect, test } from "@playwright/test";

const SAMPLE_PASTE = `
A young lady, dressed in black and heavily veiled, sat in the window and rose as we entered.
Holmes greeted her with the easy courtesy for which he was remarkable.
She told us of strange whistles in the night, of metallic clangs, and of her sister's dying words.
`.trim();

test("smoking-gun critique stage streams between emit and close", async ({ page }) => {
  await page.goto("/");

  await page.getByTestId("source-paste").fill(SAMPLE_PASTE);
  await page.getByTestId("attestation").check();
  await page.getByTestId("generate").click();

  // The new critique stage is surfaced in the progress stream.
  await expect(page.getByTestId("stage-critique-started")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("stage-critique-complete")).toBeVisible({ timeout: 30_000 });

  // The run completes through to a downloadable corpus, with no failed stage.
  await expect(page.getByTestId("stage-done-complete")).toBeVisible({ timeout: 30_000 });
  await expect(page.locator(".stage.failed")).toHaveCount(0);
  await expect(page.getByTestId("download")).toBeVisible();
});

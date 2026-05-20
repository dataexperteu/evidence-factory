/**
 * Slice 2: Intake mode switching — Paste / URL / Upload.
 *
 * These tests validate tab switching, panel visibility, URL preview flow,
 * upload file selection, and that Generate is gated correctly per mode.
 * The full pipeline run (→ download) is covered by tracer-bullet.spec.ts.
 */

import { type Page, expect, test } from "@playwright/test";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

async function gotoApp(page: Page) {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: /evidence factory/i })).toBeVisible();
}

// ---------------------------------------------------------------------------
// Tab switching and panel visibility
// ---------------------------------------------------------------------------

test("Paste tab is selected by default and shows the paste panel", async ({ page }) => {
  await gotoApp(page);

  const pasteTab = page.getByTestId("tab-paste");
  const urlTab = page.getByTestId("tab-url");
  const uploadTab = page.getByTestId("tab-upload");

  await expect(pasteTab).toHaveAttribute("aria-selected", "true");
  await expect(urlTab).toHaveAttribute("aria-selected", "false");
  await expect(uploadTab).toHaveAttribute("aria-selected", "false");

  await expect(page.getByTestId("panel-paste")).toBeVisible();
  await expect(page.getByTestId("panel-url")).not.toBeVisible();
  await expect(page.getByTestId("panel-upload")).not.toBeVisible();
});

test("Clicking URL tab shows URL panel and hides paste panel", async ({ page }) => {
  await gotoApp(page);

  await page.getByTestId("tab-url").click();

  await expect(page.getByTestId("tab-url")).toHaveAttribute("aria-selected", "true");
  await expect(page.getByTestId("panel-url")).toBeVisible();
  await expect(page.getByTestId("panel-paste")).not.toBeVisible();
  await expect(page.getByTestId("panel-upload")).not.toBeVisible();
});

test("Clicking Upload tab shows upload panel and hides others", async ({ page }) => {
  await gotoApp(page);

  await page.getByTestId("tab-upload").click();

  await expect(page.getByTestId("tab-upload")).toHaveAttribute("aria-selected", "true");
  await expect(page.getByTestId("panel-upload")).toBeVisible();
  await expect(page.getByTestId("panel-paste")).not.toBeVisible();
  await expect(page.getByTestId("panel-url")).not.toBeVisible();
});

// ---------------------------------------------------------------------------
// Paste mode Generate gating
// ---------------------------------------------------------------------------

test("Generate is disabled in paste mode until text is pasted and attested", async ({ page }) => {
  await gotoApp(page);

  const generate = page.getByTestId("generate");
  await expect(generate).toBeDisabled();

  await page.getByTestId("source-paste").fill("Some story text here.");
  await expect(generate).toBeDisabled(); // still no attestation

  await page.getByTestId("attestation").check();
  await expect(generate).toBeEnabled();
});

// ---------------------------------------------------------------------------
// URL mode Generate gating
// ---------------------------------------------------------------------------

test("Generate is disabled in URL mode until preview is fetched and attested", async ({
  page,
}) => {
  await gotoApp(page);
  await page.getByTestId("tab-url").click();

  const generate = page.getByTestId("generate");
  await expect(generate).toBeDisabled();

  await page.getByTestId("source-url").fill("https://example.com/article");
  await expect(generate).toBeDisabled(); // no preview yet, no attestation

  await page.getByTestId("attestation").check();
  await expect(generate).toBeDisabled(); // still no preview
});

test("Preview button is disabled when URL field is empty", async ({ page }) => {
  await gotoApp(page);
  await page.getByTestId("tab-url").click();

  await expect(page.getByTestId("fetch-preview")).toBeDisabled();

  await page.getByTestId("source-url").fill("https://example.com");
  await expect(page.getByTestId("fetch-preview")).toBeEnabled();
});

test("URL preview error surfaces when backend returns 400", async ({ page }) => {
  await gotoApp(page);
  await page.getByTestId("tab-url").click();

  // Use a clearly invalid URL that the backend should reject
  await page.getByTestId("source-url").fill("ftp://not-http.invalid/file");
  await page.getByTestId("fetch-preview").click();

  await expect(page.getByTestId("url-preview-error")).toBeVisible({ timeout: 10_000 });
  await expect(page.getByTestId("url-preview")).not.toBeVisible();
});

// ---------------------------------------------------------------------------
// Upload mode Generate gating
// ---------------------------------------------------------------------------

test("Generate is disabled in upload mode until file is selected and attested", async ({
  page,
}) => {
  await gotoApp(page);
  await page.getByTestId("tab-upload").click();

  const generate = page.getByTestId("generate");
  await expect(generate).toBeDisabled();

  // Select a file
  const content = Buffer.from("The quick brown fox jumped over the lazy dog.");
  await page.getByTestId("file-upload").setInputFiles({
    name: "story.txt",
    mimeType: "text/plain",
    buffer: content,
  });

  // File info should appear
  await expect(page.getByTestId("upload-info")).toBeVisible();
  await expect(generate).toBeDisabled(); // no attestation yet

  await page.getByTestId("attestation").check();
  await expect(generate).toBeEnabled();
});

// ---------------------------------------------------------------------------
// Three-tab smoke: all three tabs render and are accessible by role
// ---------------------------------------------------------------------------

test("All three intake tabs are present with correct roles", async ({ page }) => {
  await gotoApp(page);

  const tabs = page.getByRole("tab");
  await expect(tabs).toHaveCount(3);

  for (const label of ["Paste", "URL", "Upload"]) {
    await expect(page.getByRole("tab", { name: label })).toBeVisible();
  }
});

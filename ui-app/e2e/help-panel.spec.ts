import { expect, test } from "@playwright/test";

// Each test gets a fresh browser context → localStorage is clean by default.

test("help panel is visible on page load without interaction", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByTestId("help-panel")).toBeVisible();
});

test("help panel content describes purpose, input, and output", async ({ page }) => {
  await page.goto("/");
  const content = page.locator(".help-content");
  await expect(content).toContainText("synthetic forensic evidence corpus");
  await expect(content).toContainText("difficulty preset");
  await expect(content).toContainText("corpus.zip");
  await expect(content).toContainText("MANIFEST.txt");
});

test("help panel can be dismissed by clicking ×", async ({ page }) => {
  await page.goto("/");
  await page.getByTestId("help-dismiss").click();
  await expect(page.getByTestId("help-panel")).not.toBeVisible();
});

test("dismissed help panel stays gone on reload", async ({ page }) => {
  await page.goto("/");
  await page.getByTestId("help-dismiss").click();
  await page.reload();
  await expect(page.getByTestId("help-panel")).not.toBeVisible();
});

test("form is still accessible after help panel is dismissed", async ({ page }) => {
  await page.goto("/");
  await page.getByTestId("help-dismiss").click();
  await expect(page.getByTestId("generate")).toBeVisible();
});

test("help panel reappears after localStorage is cleared", async ({ page }) => {
  // Dismiss first so localStorage flag is set.
  await page.goto("/");
  await page.getByTestId("help-dismiss").click();
  await page.reload();
  await expect(page.getByTestId("help-panel")).not.toBeVisible();

  // Clear the flag — simulates a user who cleared browser data.
  await page.evaluate(() => localStorage.removeItem("help-dismissed"));
  await page.reload();
  await expect(page.getByTestId("help-panel")).toBeVisible();
});

test("dismiss button has accessible aria-label", async ({ page }) => {
  await page.goto("/");
  const btn = page.getByTestId("help-dismiss");
  await expect(btn).toHaveAttribute("aria-label", "Dismiss help");
});

test("help panel has blue banner styling", async ({ page }) => {
  await page.goto("/");
  const banner = page.getByTestId("help-panel");
  await expect(banner).toHaveClass(/help-banner/);
});

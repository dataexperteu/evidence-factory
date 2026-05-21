import { expect, test } from "@playwright/test";

test("Easy/Medium/Hard preset buttons are visible adjacent to generate", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByTestId("preset-easy")).toBeVisible();
  await expect(page.getByTestId("preset-medium")).toBeVisible();
  await expect(page.getByTestId("preset-hard")).toBeVisible();
});

test("Medium preset is active by default", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByTestId("preset-medium")).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByTestId("preset-easy")).toHaveAttribute("aria-pressed", "false");
  await expect(page.getByTestId("preset-hard")).toHaveAttribute("aria-pressed", "false");
});

test("Clicking a preset activates it and deactivates others", async ({ page }) => {
  await page.goto("/");
  await page.getByTestId("preset-hard").click();
  await expect(page.getByTestId("preset-hard")).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByTestId("preset-medium")).toHaveAttribute("aria-pressed", "false");
  await expect(page.getByTestId("preset-easy")).toHaveAttribute("aria-pressed", "false");

  await page.getByTestId("preset-easy").click();
  await expect(page.getByTestId("preset-easy")).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByTestId("preset-hard")).toHaveAttribute("aria-pressed", "false");
});

test("Advanced disclosure section is present and expandable", async ({ page }) => {
  await page.goto("/");
  const details = page.getByTestId("advanced-dials");
  await expect(details).toBeVisible();

  // Open the disclosure
  await page.getByTestId("advanced-dials-summary").click();

  // All 7 dial inputs should be visible after opening
  await expect(page.getByTestId("dial-owners-per-proposition")).toBeVisible();
  await expect(page.getByTestId("dial-min-owner-distinct")).toBeVisible();
  await expect(page.getByTestId("dial-fragmentation-factor")).toBeVisible();
  await expect(page.getByTestId("dial-dominance-margin")).toBeVisible();
  await expect(page.getByTestId("dial-target-artifact-count")).toBeVisible();
  await expect(page.getByTestId("dial-red-herring-count")).toBeVisible();
  await expect(page.getByTestId("dial-noise-count")).toBeVisible();
});

test("Selecting Hard preset updates dial values", async ({ page }) => {
  await page.goto("/");
  await page.getByTestId("advanced-dials-summary").click();

  // Start at medium (owners_per_proposition=3)
  await expect(page.getByTestId("dial-owners-per-proposition")).toHaveValue("3");

  // Switch to Hard (owners_per_proposition=5)
  await page.getByTestId("preset-hard").click();
  await expect(page.getByTestId("dial-owners-per-proposition")).toHaveValue("5");
  await expect(page.getByTestId("dial-fragmentation-factor")).toHaveValue("5");
});

test("Selecting Easy preset updates dial values", async ({ page }) => {
  await page.goto("/");
  await page.getByTestId("advanced-dials-summary").click();

  await page.getByTestId("preset-easy").click();
  await expect(page.getByTestId("dial-owners-per-proposition")).toHaveValue("2");
  await expect(page.getByTestId("dial-fragmentation-factor")).toHaveValue("2");
  await expect(page.getByTestId("dial-target-artifact-count")).toHaveValue("100");
});

test("Manual dial edit shows Custom indicator and deselects preset", async ({ page }) => {
  await page.goto("/");
  await page.getByTestId("advanced-dials-summary").click();

  // Start at medium — no "Custom" label
  await expect(page.getByTestId("preset-custom")).not.toBeVisible();

  // Edit a dial to a non-preset value
  const dial = page.getByTestId("dial-owners-per-proposition");
  await dial.fill("4");
  await dial.blur();

  // Now no preset matches → Custom indicator appears
  await expect(page.getByTestId("preset-custom")).toBeVisible();
  await expect(page.getByTestId("preset-easy")).toHaveAttribute("aria-pressed", "false");
  await expect(page.getByTestId("preset-medium")).toHaveAttribute("aria-pressed", "false");
  await expect(page.getByTestId("preset-hard")).toHaveAttribute("aria-pressed", "false");
});

test("Preset included in generate request body", async ({ page }) => {
  const requests: { url: string; body: Record<string, unknown> }[] = [];
  await page.route("/api/runs", async (route) => {
    const request = route.request();
    const body = JSON.parse(request.postData() ?? "{}") as Record<string, unknown>;
    requests.push({ url: request.url(), body });
    await route.fulfill({ status: 200, body: JSON.stringify({ run_id: "test123" }) });
  });

  await page.goto("/");

  // Select Hard preset
  await page.getByTestId("preset-hard").click();

  // Fill in paste + attest
  await page.getByTestId("tab-paste").click();
  await page.getByTestId("source-paste").fill("A witness saw something important.");
  await page.getByTestId("attestation").check();
  await page.getByTestId("generate").click();

  await page.waitForTimeout(500);

  expect(requests).toHaveLength(1);
  expect(requests[0].body.difficulty).toBe("hard");
  expect(requests[0].body.owners_per_proposition).toBe(5);
  expect(requests[0].body.fragmentation_factor).toBe(5);
});

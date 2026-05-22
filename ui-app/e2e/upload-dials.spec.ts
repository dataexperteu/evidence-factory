/**
 * Slice 14: Upload intake mode must include difficulty dials in the FormData
 * sent to POST /api/runs/upload.
 */

import { expect, test } from "@playwright/test";

test("Upload FormData includes difficulty preset field", async ({ page }) => {
  const formDataFields: Record<string, string> = {};

  await page.route("/api/runs/upload", async (route) => {
    const request = route.request();
    const body = request.postDataBuffer();
    const contentType = request.headers()["content-type"] ?? "";
    // Parse boundary from multipart content-type header
    const boundaryMatch = contentType.match(/boundary=([^\s;]+)/);
    if (body && boundaryMatch) {
      const boundary = boundaryMatch[1];
      const text = body.toString("utf-8");
      // Extract named form fields (non-file parts)
      const parts = text.split(`--${boundary}`);
      for (const part of parts) {
        const nameMatch = part.match(/Content-Disposition: form-data; name="([^"]+)"(?!.*filename)/s);
        if (nameMatch) {
          const valueMatch = part.match(/\r\n\r\n([\s\S]*?)\r\n$/);
          if (valueMatch) {
            formDataFields[nameMatch[1]] = valueMatch[1].trim();
          }
        }
      }
    }
    await route.fulfill({ status: 200, body: JSON.stringify({ run_id: "upload-test-123" }) });
  });

  await page.goto("/");
  await page.getByTestId("tab-upload").click();

  // Select Hard preset
  await page.getByTestId("preset-hard").click();

  // Upload a file
  const content = Buffer.from("Evidence text for upload test.");
  await page.getByTestId("file-upload").setInputFiles({
    name: "evidence.txt",
    mimeType: "text/plain",
    buffer: content,
  });

  await page.getByTestId("attestation").check();
  await page.getByTestId("generate").click();

  await page.waitForTimeout(500);

  expect(formDataFields["difficulty"]).toBe("hard");
  expect(formDataFields["attestation_checked"]).toBe("true");
});

test("Upload FormData includes all seven dial fields", async ({ page }) => {
  const formDataFields: Record<string, string> = {};

  await page.route("/api/runs/upload", async (route) => {
    const request = route.request();
    const body = request.postDataBuffer();
    const contentType = request.headers()["content-type"] ?? "";
    const boundaryMatch = contentType.match(/boundary=([^\s;]+)/);
    if (body && boundaryMatch) {
      const boundary = boundaryMatch[1];
      const text = body.toString("utf-8");
      const parts = text.split(`--${boundary}`);
      for (const part of parts) {
        const nameMatch = part.match(/Content-Disposition: form-data; name="([^"]+)"(?!.*filename)/s);
        if (nameMatch) {
          const valueMatch = part.match(/\r\n\r\n([\s\S]*?)\r\n$/);
          if (valueMatch) {
            formDataFields[nameMatch[1]] = valueMatch[1].trim();
          }
        }
      }
    }
    await route.fulfill({ status: 200, body: JSON.stringify({ run_id: "upload-dials-456" }) });
  });

  await page.goto("/");
  await page.getByTestId("tab-upload").click();

  // Use Easy preset so values are distinct from Hard
  await page.getByTestId("preset-easy").click();

  const content = Buffer.from("Another test story.");
  await page.getByTestId("file-upload").setInputFiles({
    name: "story.txt",
    mimeType: "text/plain",
    buffer: content,
  });

  await page.getByTestId("attestation").check();
  await page.getByTestId("generate").click();

  await page.waitForTimeout(500);

  // All seven dial fields must be present in the FormData
  for (const field of [
    "owners_per_proposition",
    "min_owner_distinct",
    "fragmentation_factor",
    "dominance_margin",
    "target_artifact_count",
    "red_herring_count",
    "noise_count",
  ]) {
    expect(formDataFields[field], `FormData missing field: ${field}`).toBeDefined();
  }

  // Easy preset values should match PRESET_DIALS.easy
  expect(formDataFields["owners_per_proposition"]).toBe("2");
  expect(formDataFields["noise_count"]).toBe("80");
  expect(formDataFields["target_artifact_count"]).toBe("100");
});

test("Upload FormData reflects custom dial values", async ({ page }) => {
  const formDataFields: Record<string, string> = {};

  await page.route("/api/runs/upload", async (route) => {
    const request = route.request();
    const body = request.postDataBuffer();
    const contentType = request.headers()["content-type"] ?? "";
    const boundaryMatch = contentType.match(/boundary=([^\s;]+)/);
    if (body && boundaryMatch) {
      const boundary = boundaryMatch[1];
      const text = body.toString("utf-8");
      const parts = text.split(`--${boundary}`);
      for (const part of parts) {
        const nameMatch = part.match(/Content-Disposition: form-data; name="([^"]+)"(?!.*filename)/s);
        if (nameMatch) {
          const valueMatch = part.match(/\r\n\r\n([\s\S]*?)\r\n$/);
          if (valueMatch) {
            formDataFields[nameMatch[1]] = valueMatch[1].trim();
          }
        }
      }
    }
    await route.fulfill({ status: 200, body: JSON.stringify({ run_id: "custom-789" }) });
  });

  await page.goto("/");
  await page.getByTestId("tab-upload").click();

  // Open advanced dials and set a custom value
  await page.getByTestId("advanced-dials-summary").click();
  const noiseDial = page.getByTestId("dial-noise-count");
  await noiseDial.fill("42");
  await noiseDial.blur();

  // Custom indicator should appear (no preset matches)
  await expect(page.getByTestId("preset-custom")).toBeVisible();

  const content = Buffer.from("Custom dials test.");
  await page.getByTestId("file-upload").setInputFiles({
    name: "custom.txt",
    mimeType: "text/plain",
    buffer: content,
  });

  await page.getByTestId("attestation").check();
  await page.getByTestId("generate").click();

  await page.waitForTimeout(500);

  expect(formDataFields["noise_count"]).toBe("42");
  // difficulty should fall back to "medium" when no preset matches
  expect(formDataFields["difficulty"]).toBe("medium");
});

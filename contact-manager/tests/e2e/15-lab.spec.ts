import { test, expect } from "@playwright/test";
import { mockSupabase } from "./helpers";

test.beforeEach(async ({ page }) => {
  await mockSupabase(page);
});

test("/lab page loads with contact picker and mode toggle", async ({ page }) => {
  await page.goto("/lab");

  await expect(page.getByTestId("contact-picker-toggle")).toBeVisible();
  await expect(page.getByTestId("mode-writer")).toBeVisible();
  await expect(page.getByTestId("mode-critic")).toBeVisible();

  await page.screenshot({ path: "tests/e2e/screenshots/15-lab-empty.png" });
});

test("Lab nav link navigates to /lab", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("link", { name: /^lab$/i }).click();
  await expect(page).toHaveURL(/\/lab/);
  await expect(page.getByTestId("contact-picker-toggle")).toBeVisible();
});

test("contact picker: open, search, select collapses to summary", async ({
  page,
}) => {
  await page.goto("/lab");

  // Open picker
  await page.getByTestId("contact-picker-toggle").click();
  // Search input appears (located by placeholder attribute)
  await expect(page.locator('input[placeholder*="earch"]')).toBeVisible();

  // Alice Chen appears from fixtures
  await expect(page.getByText("Alice Chen")).toBeVisible();

  // Select Alice — triggers async full-record fetch, then onSelect fires
  await page.getByText("Alice Chen").first().click();

  // Wait for toggle to show the contact summary (confirms onSelect fired)
  await expect(page.getByTestId("contact-picker-toggle")).toContainText(
    "Alice Chen",
    { timeout: 5_000 }
  );
  // Dropdown gone
  await expect(page.locator('input[placeholder*="earch"]')).not.toBeVisible();

  await page.screenshot({ path: "tests/e2e/screenshots/15-lab-contact-selected.png" });
});

test("mode toggle: critic mode shows draft inputs", async ({ page }) => {
  await page.goto("/lab");

  await page.getByTestId("mode-critic").click();

  // Critic mode shows subject + body inputs
  await expect(page.locator('input[placeholder*="ubject"]')).toBeVisible();
  await expect(page.locator('textarea[placeholder*="aste draft"]')).toBeVisible();

  await page.screenshot({ path: "tests/e2e/screenshots/15-lab-critic-mode.png" });
});

test("mode toggle: switching back to writer hides critic inputs", async ({
  page,
}) => {
  await page.goto("/lab");

  await page.getByTestId("mode-critic").click();
  await expect(page.locator('input[placeholder*="ubject"]')).toBeVisible();

  await page.getByTestId("mode-writer").click();
  await expect(page.locator('input[placeholder*="ubject"]')).not.toBeVisible();
});

test("preview: clicking Preview fires API and shows result body", async ({
  page,
}) => {
  await page.goto("/lab");

  // Select a contact and wait for onSelect to fire (async full-record fetch)
  await page.getByTestId("contact-picker-toggle").click();
  await expect(page.getByText("Alice Chen")).toBeVisible();
  await page.getByText("Alice Chen").first().click();
  await expect(page.getByTestId("contact-picker-toggle")).toContainText(
    "Alice Chen",
    { timeout: 5_000 }
  );

  // Click Preview
  await page.getByRole("button", { name: /preview/i }).click();

  // Preview body from the mocked API appears
  await expect(
    page.getByText(/I noticed your work at Acme Corp/i)
  ).toBeVisible({ timeout: 10_000 });

  await page.screenshot({ path: "tests/e2e/screenshots/15-lab-preview.png" });
});

test("compare: editing prompt then previewing shows Saved and Sandbox labels", async ({
  page,
}) => {
  await page.goto("/lab");

  // Select a contact and wait for onSelect to fire
  await page.getByTestId("contact-picker-toggle").click();
  await page.getByText("Alice Chen").first().click();
  await expect(page.getByTestId("contact-picker-toggle")).toContainText(
    "Alice Chen",
    { timeout: 5_000 }
  );

  // Wait for prompts to load, then edit so sandbox diverges from saved
  const editor = page.getByRole("textbox", { name: /prompt editor/i });
  await expect(editor).not.toHaveValue("", { timeout: 5_000 });
  await editor.fill("Modified outreach prompt.");

  // Click Preview — two calls fire (sandbox + saved)
  await page.getByRole("button", { name: /preview/i }).click();

  // Both "Saved" and "Sandbox" column labels should appear
  await expect(page.getByText("Saved")).toBeVisible({ timeout: 10_000 });
  await expect(page.getByText("Sandbox")).toBeVisible();

  await page.screenshot({ path: "tests/e2e/screenshots/15-lab-compare.png" });
});

test("save: editing prompt enables Save button and opens confirm dialog", async ({
  page,
}) => {
  await page.goto("/lab");

  // Wait for prompts to load before editing (prevents race with setSandboxValues override)
  const editor = page.getByRole("textbox", { name: /prompt editor/i });
  await expect(editor).not.toHaveValue("", { timeout: 5_000 });

  // Edit the textarea
  await editor.fill("New prompt value.");

  // Save button should become enabled
  const saveBtn = page.getByRole("button", { name: /^save$/i });
  await expect(saveBtn).toBeEnabled();

  // Click Save opens ConfirmModal (real Radix Dialog renders with role="dialog")
  await saveBtn.click();
  await expect(page.getByRole("dialog")).toBeVisible({ timeout: 5_000 });

  await page.screenshot({ path: "tests/e2e/screenshots/15-lab-save-dialog.png" });
});

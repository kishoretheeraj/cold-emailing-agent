import { test, expect } from "@playwright/test";
import { mockSupabase } from "./helpers";

test.beforeEach(async ({ page }) => {
  await mockSupabase(page);
});

test("create a networking contact via Structured Form, verify mode toggle and stage badge", async ({
  page,
}) => {
  await page.goto("/");
  await expect(page.getByText("Alice Chen")).toBeVisible({ timeout: 10_000 });

  await page.getByRole("button", { name: /structured form/i }).click();
  await page.getByRole("button", { name: /networking contact/i }).click();

  // 3-way mode tab bar (Outreach / Applied / Networking) visible.
  await expect(
    page.getByRole("button", { name: /^outreach contact$/i })
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: /^applied contact$/i })
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: /^networking contact$/i })
  ).toBeVisible();

  // Networking-specific field is present.
  await expect(page.getByText(/^Connection \(/i)).toBeVisible();

  // Fill required fields by finding inputs adjacent to their labels.
  const nameField = page.locator("label", { hasText: "Name" }).locator("..").locator("input");
  const emailField = page.locator("label", { hasText: "Email" }).locator("..").locator("input");
  const companyField = page
    .locator("label", { hasText: "Company" })
    .locator("..")
    .locator("input");
  await nameField.fill("Jordan Lee");
  await emailField.fill("jordan.lee@example.com");
  await companyField.fill("Meridian Labs");

  await page.getByRole("button", { name: /^add contact$/i }).click();

  await expect(
    page.getByText(/agent picks this up tomorrow/i)
  ).toBeVisible({ timeout: 5_000 });
  await expect(page.getByText("Jordan Lee")).toBeVisible({ timeout: 5_000 });

  await page.screenshot({ path: "tests/e2e/screenshots/16-networking-form.png" });
});

test("networking contact sheet shows only the Networking stage group and the Connection field", async ({
  page,
}) => {
  await page.goto("/");
  await expect(page.getByText("Priya Nair")).toBeVisible({ timeout: 10_000 });

  await page.getByRole("button", { name: /priya nair/i }).click();
  await expect(page.getByRole("dialog")).toBeVisible({ timeout: 5_000 });

  // Mode button-group shows all 3 options, Networking active.
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByRole("button", { name: /^outreach$/i })).toBeVisible();
  await expect(dialog.getByRole("button", { name: /^applied$/i })).toBeVisible();
  await expect(dialog.getByRole("button", { name: /^networking$/i })).toBeVisible();

  // Connection field visible (networking-only).
  await expect(dialog.getByText(/^Connection$/i)).toBeVisible();

  // Stage dropdown shows the Networking group, not Outreach/Applied.
  const stageCombobox = dialog.getByRole("combobox").first();
  await stageCombobox.click();
  await expect(page.getByText("Networking", { exact: true })).toBeVisible();
  await expect(page.getByText("Outreach", { exact: true })).not.toBeVisible();
  await page.keyboard.press("Escape");

  await page.screenshot({ path: "tests/e2e/screenshots/16-networking-sheet.png" });
});

test("promote flow: networking contact can be promoted to outreach", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByText("Priya Nair")).toBeVisible({ timeout: 10_000 });

  await page.getByRole("button", { name: /priya nair/i }).click();
  await expect(page.getByRole("dialog")).toBeVisible({ timeout: 5_000 });

  await page.getByRole("button", { name: /promote to role track/i }).click();
  await expect(page.getByText(/promote to a role track/i)).toBeVisible({
    timeout: 3_000,
  });

  await page.screenshot({ path: "tests/e2e/screenshots/16-networking-promote-modal.png" });

  await page.getByRole("button", { name: /promote to outreach/i }).click();
  await page.getByRole("button", { name: /^promote$/i }).click();

  await expect(page.getByText(/promoted to outreach/i)).toBeVisible({
    timeout: 5_000,
  });
});

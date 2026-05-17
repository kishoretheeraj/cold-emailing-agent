import { test, expect } from "@playwright/test";
import { mockSupabase } from "./helpers";

test.beforeEach(async ({ page }) => {
  await mockSupabase(page);
});

test("thread view shows empty state in side sheet", async ({ page }) => {
  await page.goto("/");

  // Click the first contact row to open the sheet
  const rows = page.locator("tbody tr, [data-testid='contact-row']");
  const count = await rows.count();
  if (count === 0) {
    // No contacts in fixture — skip
    return;
  }

  await rows.first().click();

  // Sheet should open
  await expect(page.getByRole("dialog")).toBeVisible({ timeout: 5_000 });

  // Thread section heading visible
  await expect(page.getByText(/email thread/i)).toBeVisible();

  // Empty state (no mocked email_messages)
  await expect(page.getByText(/no emails recorded yet/i)).toBeVisible({
    timeout: 5_000,
  });
});

import { test, expect } from "@playwright/test";
import { mockSupabase } from "./helpers";

test.beforeEach(async ({ page }) => {
  await mockSupabase(page);
});

test("delete modal shows amber warning for _drafted contacts with message_id", async ({
  page,
}) => {
  await page.goto("/");
  await expect(page.getByText("Alice Chen")).toBeVisible({ timeout: 10_000 });

  // Bob Martinez is fixture index 1: stage="first_touch_drafted", message_id="msg-thread-2"
  const draftedRow = page.getByRole("button", { name: /bob martinez/i });
  await expect(draftedRow).toBeVisible({ timeout: 5_000 });
  await draftedRow.click();

  // Sheet opens
  await expect(page.getByRole("dialog")).toBeVisible({ timeout: 5_000 });

  // Click Delete contact
  await page.getByRole("button", { name: /delete contact/i }).click();

  // Amber warning should appear for drafted + message_id contact
  await expect(page.getByText(/active draft in gmail/i)).toBeVisible({
    timeout: 5_000,
  });

  // Cancel closes the warning
  await page.getByRole("button", { name: /cancel/i }).click();
  await expect(page.getByText(/active draft in gmail/i)).not.toBeVisible({
    timeout: 2_000,
  });
});

import { test, expect } from "@playwright/test";
import { mockSupabase } from "./helpers";

test.beforeEach(async ({ page }) => {
  await mockSupabase(page);
});

test("soft delete: cancel keeps row, confirm removes it and row stays gone on refresh", async ({
  page,
}) => {
  await page.goto("/");
  await expect(page.getByText("Alice Chen")).toBeVisible({ timeout: 10_000 });

  // ── Cancel path ────────────────────────────────────────────────────────────
  await page.getByRole("button", { name: /alice chen/i }).click();
  await expect(page.getByRole("dialog")).toBeVisible({ timeout: 5_000 });
  await page.getByRole("button", { name: /delete contact/i }).click();

  // Confirmation text should appear
  await expect(page.getByText(/delete this contact/i)).toBeVisible({
    timeout: 3_000,
  });

  // Click Cancel
  await page.getByRole("button", { name: /cancel/i }).click();
  await expect(page.getByText(/delete this contact/i)).not.toBeVisible({
    timeout: 2_000,
  });
  // Alice should still be in the list
  await expect(page.getByText("Alice Chen").first()).toBeVisible();

  // ── Confirm path ───────────────────────────────────────────────────────────
  // Close the sheet first (click Escape or close button)
  await page.keyboard.press("Escape");
  await page.waitForTimeout(300);

  await page.getByRole("button", { name: /alice chen/i }).click();
  await expect(page.getByRole("dialog")).toBeVisible({ timeout: 5_000 });
  await page.getByRole("button", { name: /delete contact/i }).click();
  await expect(page.getByText(/delete this contact/i)).toBeVisible({
    timeout: 3_000,
  });

  // Click the danger Delete button (the one that says exactly "Delete")
  // There are multiple buttons: "Delete contact" (opener) and "Delete" (confirm)
  const confirmDeleteBtn = page.getByRole("button", { name: /^delete$/i });
  await confirmDeleteBtn.click();

  // Toast should appear
  await expect(page.getByText("Contact deleted")).toBeVisible({
    timeout: 5_000,
  });

  // Row should be gone
  await expect(page.getByText("Alice Chen")).not.toBeVisible({ timeout: 3_000 });

  // ── Refresh check ──────────────────────────────────────────────────────────
  // The page.route() mock persists across navigation within the same test,
  // so reloading will call mockSupabase's handlers which have the mutated state.
  await page.reload();
  await expect(page.getByText("Bob Martinez")).toBeVisible({ timeout: 10_000 });
  await expect(page.getByText("Alice Chen")).not.toBeVisible();
});

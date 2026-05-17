import { test, expect } from "@playwright/test";
import { mockSupabase } from "./helpers";

test.beforeEach(async ({ page }) => {
  await mockSupabase(page);
});

test("/runs page loads and shows Activity heading", async ({ page }) => {
  await page.goto("/runs");
  await expect(page.getByRole("heading", { name: /activity/i })).toBeVisible();
});

test("/runs page shows empty state when no events", async ({ page }) => {
  await page.goto("/runs");
  await expect(page.getByText(/no events yet/i)).toBeVisible({ timeout: 8_000 });
});

test("/runs page status chips are present", async ({ page }) => {
  await page.goto("/runs");
  await expect(page.getByRole("button", { name: /^all$/i })).toBeVisible();
  await expect(page.getByRole("button", { name: /^failed$/i })).toBeVisible();
  await expect(page.getByRole("button", { name: /^blocked$/i })).toBeVisible();
});

test("Activity nav link from home navigates to /runs", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("link", { name: /activity/i }).click();
  await expect(page).toHaveURL(/\/runs/);
  await expect(page.getByRole("heading", { name: /activity/i })).toBeVisible({
    timeout: 8_000,
  });
});

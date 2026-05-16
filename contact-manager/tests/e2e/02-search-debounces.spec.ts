import { test, expect } from "@playwright/test";
import { mockSupabase } from "./helpers";

test.beforeEach(async ({ page }) => {
  await mockSupabase(page);
});

test("search debounces 300ms then filters list, clear restores all", async ({
  page,
}) => {
  await page.goto("/");
  await expect(page.getByText("Alice Chen")).toBeVisible({ timeout: 10_000 });

  const searchInput = page.getByPlaceholder("Search by name or company");

  // Type a term that matches Alice Chen uniquely
  await searchInput.fill("Alice");

  // Within 200ms the debounce should not have fired
  await page.waitForTimeout(150);
  await expect(page.getByText("Bob Martinez")).toBeVisible();

  // After 350ms debounce fires, list filters
  await page.waitForTimeout(200);
  await expect(page.getByText("Alice Chen")).toBeVisible({ timeout: 5_000 });
  await expect(page.getByText("Bob Martinez")).not.toBeVisible({ timeout: 2_000 });

  // Clear search — full list returns
  await searchInput.clear();
  await page.waitForTimeout(350);
  await expect(page.getByText("Bob Martinez")).toBeVisible({ timeout: 5_000 });
});

import { test, expect } from "@playwright/test";
import { mockSupabase } from "./helpers";

test.beforeEach(async ({ page }) => {
  await mockSupabase(page);
});

test("list loads contacts, scroll loads more, end-of-list appears", async ({
  page,
}) => {
  await page.goto("/");

  // Wait for fixture contacts to appear (first contact in fixture is Alice Chen)
  await expect(page.getByText("Alice Chen")).toBeVisible({ timeout: 10_000 });

  // "All contacts loaded" should NOT be visible yet (50 contacts, page size 30)
  await expect(page.getByText("All contacts loaded")).not.toBeVisible();

  // Scroll to bottom to trigger IntersectionObserver
  await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));

  // Wait for remaining contacts to load
  await expect(page.getByText("All contacts loaded")).toBeVisible({
    timeout: 10_000,
  });
});

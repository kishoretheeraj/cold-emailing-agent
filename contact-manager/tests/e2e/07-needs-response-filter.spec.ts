import { test, expect } from "@playwright/test";
import { mockSupabase } from "./helpers";

test.beforeEach(async ({ page }) => {
  await mockSupabase(page);
});

test("Needs response toggle is visible in filter bar", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByText("Needs response")).toBeVisible();
});

test("Needs response toggle can be clicked without error", async ({ page }) => {
  await page.goto("/");
  // Click the toggle button (ToggleSwitch renders a button next to its label text)
  const toggle = page.locator("button").filter({ hasText: "Needs response" });
  // If not found as button, fall back to clicking the label text area
  const count = await toggle.count();
  if (count > 0) {
    await toggle.click();
  } else {
    await page.getByText("Needs response").click();
  }
  // No error should occur
  await page.waitForTimeout(200);
});

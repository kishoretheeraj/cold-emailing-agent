import { test, expect } from "@playwright/test";
import { mockSupabase } from "./helpers";

test.beforeEach(async ({ page }) => {
  await mockSupabase(page);
});

test("tier and mode pills filter list, Clear filters resets", async ({
  page,
}) => {
  await page.goto("/");
  await expect(page.getByText("Alice Chen")).toBeVisible({ timeout: 10_000 });

  // Click Tier 1 pill — should show only T1 badges
  const tier1Pill = page.getByRole("button", { name: "1" }).first();
  await tier1Pill.click();
  await expect(page.getByText("T1").first()).toBeVisible({ timeout: 5_000 });
  // T2 badges should not be visible when only T1 is selected
  await expect(page.getByText("T2").first()).not.toBeVisible({ timeout: 3_000 });

  // Click Tier 1 again to deselect — T2 badges return
  await tier1Pill.click();
  await expect(page.getByText("T2").first()).toBeVisible({ timeout: 5_000 });

  // Click Outreach mode pill to filter to outreach contacts
  const outreachPill = page.getByRole("button", { name: /^outreach$/i });
  await outreachPill.click();
  await page.waitForTimeout(300);

  // Click Dartmouth toggle
  const dartToggle = page.getByRole("button", { name: /dartmouth/i });
  await dartToggle.click();
  await page.waitForTimeout(300);

  // Clear filters button should appear
  const clearBtn = page.getByRole("button", { name: /clear filters/i }).first();
  await expect(clearBtn).toBeVisible({ timeout: 3_000 });

  await clearBtn.click();
  await page.waitForTimeout(400);

  // After clear, multiple tiers visible
  await expect(page.getByText("T1").first()).toBeVisible({ timeout: 5_000 });
  await expect(page.getByText("T2").first()).toBeVisible({ timeout: 5_000 });

  // Clear button should be gone from filter bar (but EmptyState's might not be relevant here)
  await expect(clearBtn).not.toBeVisible({ timeout: 3_000 });
});

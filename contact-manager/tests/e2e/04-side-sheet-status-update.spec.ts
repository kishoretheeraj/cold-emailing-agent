import { test, expect } from "@playwright/test";
import { mockSupabase } from "./helpers";

test.beforeEach(async ({ page }) => {
  await mockSupabase(page);
});

test("row click opens side sheet, stage update is optimistic (no extra GET)", async ({
  page,
}) => {
  await page.goto("/");
  await expect(page.getByText("Alice Chen")).toBeVisible({ timeout: 10_000 });

  // Count GET /contacts requests during the update
  let contactGetCount = 0;
  page.on("request", (req) => {
    if (req.url().includes("/rest/v1/contacts") && req.method() === "GET") {
      contactGetCount++;
    }
  });

  const initialGetCount = contactGetCount;

  // Click a contact row to open the side sheet
  await page.getByRole("button", { name: /alice chen/i }).click();
  await expect(page.getByRole("dialog")).toBeVisible({ timeout: 5_000 });

  // Change stage using the combobox (Vaul drawer content has a combobox)
  const combobox = page.getByRole("combobox").first();
  await combobox.click();

  // Pick any available option from the dropdown
  const firstOption = page.getByRole("option").first();
  await firstOption.click();

  // Wait briefly for the optimistic update
  await page.waitForTimeout(500);

  // No GET request to /contacts should have fired during the update
  expect(contactGetCount).toBe(initialGetCount);

  // Sheet should still be open
  await expect(page.getByRole("dialog")).toBeVisible();
});

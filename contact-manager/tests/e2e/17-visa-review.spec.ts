import { test, expect } from "@playwright/test";
import { mockSupabase } from "./helpers";

test.beforeEach(async ({ page }) => {
  await mockSupabase(page);
});

test("nav shows a pending-count badge and links to the review page", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByText("Alice Chen")).toBeVisible({ timeout: 10_000 });

  const visaLink = page.getByRole("link", { name: /^visa/i });
  await expect(visaLink).toBeVisible();
  await expect(visaLink).toContainText("1"); // one needs_review row in the fixture

  await visaLink.click();
  await expect(page).toHaveURL(/\/visa-review$/);
});

test("confirm, reject-equivalent, and summary flow for a needs_review company", async ({ page }) => {
  await page.goto("/visa-review");

  await expect(page.getByText("Bolt Inc")).toBeVisible({ timeout: 10_000 });
  await expect(page.getByText("Bolt Industries LLC")).toBeVisible();
  await expect(page.getByText(/84% match/i)).toBeVisible();

  await page.screenshot({ path: "tests/e2e/screenshots/17-visa-review-card.png" });

  await page.getByRole("button", { name: /^confirm$/i }).click();

  await expect(page.getByText(/review complete/i)).toBeVisible({ timeout: 5_000 });
  const confirmedCard = page.getByText("Confirmed").locator("..");
  await expect(confirmedCard).toContainText("1");

  await page.screenshot({ path: "tests/e2e/screenshots/17-visa-review-summary.png" });
});

test("skip leaves the company in needs_review and moves to the next card", async ({ page }) => {
  await page.goto("/visa-review");
  await expect(page.getByText("Bolt Inc")).toBeVisible({ timeout: 10_000 });

  await page.getByRole("button", { name: /^skip$/i }).click();

  await expect(page.getByText(/review complete/i)).toBeVisible({ timeout: 5_000 });
  const skippedCard = page.getByText("Skipped").locator("..");
  await expect(skippedCard).toContainText("1");
});

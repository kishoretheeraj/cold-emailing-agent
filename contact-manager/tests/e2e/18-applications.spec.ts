import { test, expect } from "@playwright/test";
import { mockSupabase } from "./helpers";

test.describe("Applications page", () => {
  test.beforeEach(async ({ page }) => {
    await mockSupabase(page);
    await page.route("**/api/applications", async (route) => {
      if (route.request().method() === "GET") {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            applications: [
              { id: "1", contact_id: null, company: "Acme", role: "PM", job_url: null,
                source: "manual", stage: "saved", applied_date: null, notes: null,
                posting_snapshot: null, created_at: "2026-08-26T00:00:00Z",
                updated_at: "2026-08-26T00:00:00Z" },
            ],
          }),
        });
      } else {
        await route.continue();
      }
    });
  });

  test("shows the applications table and nav link", async ({ page }) => {
    await page.goto("/applications");
    await expect(page.getByRole("link", { name: "Applications" })).toBeVisible();
    await expect(page.getByText("Acme")).toBeVisible();
    await page.screenshot({ path: "tests/e2e/screenshots/18-applications.png" });
  });
});

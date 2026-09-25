import { test, expect } from "@playwright/test";
import { mockSupabase } from "./helpers";

test.describe("Applications page", () => {
  test.beforeEach(async ({ page }) => {
    await mockSupabase(page);
    const applicationsHandler = async (route: import("@playwright/test").Route) => {
      if (route.request().method() === "GET") {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            applications: [
              { id: "1", contact_id: null, company: "Acme", role: "PM",
                job_url: "https://jobs.acme.example/1", source: "manual", source_channel: null,
                stage: "saved", applied_date: null, notes: null,
                posting_snapshot: { description: "Own the roadmap.", location: "Remote" },
                resume_file_ref: null, cover_letter_file_ref: null,
                resume_cost_usd: null, resume_tokens_input: null, resume_tokens_output: null,
                pick_verdict: null, pick_score: null, pick_reasoning: null,
                apply_preview: null, apply_blocked_reason: null, approved_at: null,
                created_at: "2026-08-26T00:00:00Z", updated_at: "2026-08-26T00:00:00Z" },
            ],
          }),
        });
      } else {
        await route.continue();
      }
    };
    // M10: Playwright's page.route() glob matching does NOT automatically match a URL that has
    // a query string appended to the base path -- register both forms so a later task's
    // ?stage=/?source= fetches (Task 6's filters, Task 7's polling) are covered by the exact
    // same handler regardless of whether Playwright's glob semantics would otherwise miss the
    // querystring variant. Cheap insurance either way.
    await page.route("**/api/applications", applicationsHandler);
    await page.route("**/api/applications?*", applicationsHandler);
    await page.route("**/api/applications/1/files", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          resume_url: null,
          resume_error: false,
          cover_letter_url: null,
          cover_letter_error: false,
        }),
      });
    });
    await page.route("**/api/system-health", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ health: [] }),
      });
    });
  });

  test("shows the applications table and nav link", async ({ page }) => {
    await page.goto("/applications");
    await expect(page.getByRole("link", { name: "Applications" })).toBeVisible();
    await expect(page.getByText("Acme")).toBeVisible();
    await page.screenshot({ path: "tests/e2e/screenshots/18-applications.png" });
  });

  test("opens the detail sheet and shows job details on View click", async ({ page }) => {
    await page.goto("/applications");
    await page.getByRole("button", { name: "View" }).click();
    await expect(page.getByText("Own the roadmap.")).toBeVisible();
    await expect(page.getByText("Remote")).toBeVisible();
    await expect(page.getByText("No resume on file yet.")).toBeVisible();
    await expect(page.getByText("Not yet scored.")).toBeVisible();
    await page.screenshot({ path: "tests/e2e/screenshots/18-applications-detail-sheet.png" });
  });
});

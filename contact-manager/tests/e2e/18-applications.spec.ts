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
              { id: "2", contact_id: null, company: "Ashby Co", role: "PM",
                job_url: "https://jobs.example/2", source: "jobright", source_channel: null,
                stage: "ready_to_submit", applied_date: null, notes: null,
                posting_snapshot: null, resume_file_ref: null, cover_letter_file_ref: null,
                resume_cost_usd: null, resume_tokens_input: null, resume_tokens_output: null,
                pick_verdict: "strong", pick_score: 0.9, pick_reasoning: "Great fit.",
                apply_preview: { platform: "ashby", field_values: {}, eligibility_answers: {}, screening_answers: {} },
                apply_blocked_reason: null, approved_at: null,
                created_at: "2026-08-30T00:00:00Z", updated_at: "2026-08-30T00:00:00Z" },
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
    await page.route("**/api/applications/2/files", async (route) => {
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
    await page.route("**/api/applications/2/submit", async (route) => {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ ok: true }) });
    });
    await page.route("**/api/applications/2", async (route) => {
      // I8's single-row polling target. Distinct from "**/api/applications" (the list, no
      // trailing path segment) and from "**/api/applications/2/submit" / ".../2/files" (both
      // have an extra path segment) -- Playwright's glob match requires the URL to end exactly
      // where each pattern ends, so these four registrations don't collide.
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          application: { id: "2", stage: "applied", apply_blocked_reason: null },
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

  test("shows the source and filed-via columns and refetches with filters (U7/U8/U12)", async ({
    page,
  }) => {
    const requestUrls: string[] = [];
    page.on("request", (req) => {
      if (req.url().includes("/api/applications")) requestUrls.push(req.url());
    });
    await page.goto("/applications");
    const row = page.locator("tr", { hasText: "Acme" });
    await expect(row.getByText("manual")).toBeVisible();

    await page.getByTestId("stage-filter").getByRole("combobox").click();
    await page.getByRole("option", { name: "Applied" }).click();
    await expect
      .poll(() => requestUrls[requestUrls.length - 1] ?? "")
      .toContain("stage=applied");

    await page.getByTestId("source-filter").getByRole("combobox").click();
    await page.getByRole("option", { name: "linkedin" }).click();
    await expect
      .poll(() => requestUrls[requestUrls.length - 1] ?? "")
      .toContain("source=linkedin");
    expect(requestUrls[requestUrls.length - 1]).toContain("stage=applied");

    // The poll above only confirms the refetch was *issued*, not that it resolved --
    // ApplicationsPage shows a "Loading..." state while the request is in flight. Wait for
    // the actual table content (the row's "manual" Source cell, same text the assertion
    // above already checks) to be visible again before screenshotting, so the screenshot
    // captures the rendered, filtered table rather than the loading placeholder. (Not using
    // getByText("—") here -- Pick/Blocked/Filed-via all render "—" for this fixture row, so
    // that locator would match 3 elements and fail Playwright's strict mode.)
    await expect(row.getByText("manual")).toBeVisible();
    const filedViaCell = row.locator("td").nth(7);
    await expect(filedViaCell).toHaveText("—");

    await page.screenshot({ path: "tests/e2e/screenshots/18-applications-filters.png" });
  });

  test("opens the detail sheet and shows job details on View click", async ({ page }) => {
    await page.goto("/applications");
    await page.getByRole("row", { name: /Acme/ }).getByRole("button", { name: "View" }).click();
    await expect(page.getByText("Own the roadmap.")).toBeVisible();
    await expect(page.getByText("Remote")).toBeVisible();
    await expect(page.getByText("No resume on file yet.")).toBeVisible();
    await expect(page.getByText("Not yet scored.")).toBeVisible();
    await page.screenshot({ path: "tests/e2e/screenshots/18-applications-detail-sheet.png" });
  });

  test("confirms before submitting and shows the confirm dialog copy", async ({ page }) => {
    await page.goto("/applications");
    await page.getByRole("button", { name: "Approve & Submit" }).click();
    await expect(page.getByText("Submit this application?")).toBeVisible();
    // I10: getByText(/Ashby Co/) alone matches both the table cell AND the modal -- scope to
    // the dialog.
    await expect(page.getByRole("dialog").getByText(/Ashby Co/)).toBeVisible();
    await page.getByRole("dialog").getByRole("button", { name: "Approve & Submit" }).click();
    await expect(page.getByText(/watching for it to land/i)).toBeVisible();
    await page.screenshot({ path: "tests/e2e/screenshots/18-applications-confirm-modal.png" });
  });
});

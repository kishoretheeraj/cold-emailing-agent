import { test, expect, type Route } from "@playwright/test";
import { mockSupabase } from "./helpers";

// The review inbox is the home page (Beelink dashboard); the contacts app moved to /?view=contacts.
// A failed row lands in Blocked because ?view=queue now returns failed_* and preparing rows too.
const base = {
  contact_id: null, source: "jobright", source_channel: null, applied_date: null, notes: null,
  posting_snapshot: null, resume_file_ref: "r.pdf", cover_letter_file_ref: "c.pdf", pick_reasoning: null,
  approved_at: null, approved_revision_hash: null, created_at: "2026-10-15T00:00:00Z", updated_at: "2026-10-15T00:00:00Z",
};
const rows = [
  { ...base, id: "51", company: "Northwind", role: "Product Manager", job_url: "https://jobs.lever.co/northwind/1",
    stage: "ready_to_submit", automation_status: "ready_for_review", pick_verdict: "strong", pick_score: 0.9,
    preview_revision_hash: "e".repeat(64), apply_blocked_reason: null,
    apply_preview: { platform: "lever", field_values: {}, eligibility_answers: {}, screening_answers: {} } },
  { ...base, id: "52", company: "Contoso", role: "Associate Product Manager", job_url: "https://boards.greenhouse.io/contoso/1",
    stage: "saved", automation_status: "failed_retryable", pick_verdict: "strong", pick_score: 0.8,
    preview_revision_hash: null, apply_preview: null, apply_blocked_reason: "Browser crashed while filling the form" },
  { ...base, id: "53", company: "Fabrikam", role: "Product Manager", job_url: "https://boards.greenhouse.io/fabrikam/1",
    stage: "saved", automation_status: "preparing", pick_verdict: "strong", pick_score: 0.7,
    preview_revision_hash: null, apply_preview: null, apply_blocked_reason: null },
];

test.beforeEach(async ({ page }) => {
  await mockSupabase(page);
  const list = (route: Route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ applications: rows }) });
  await page.route("**/api/applications?*", list);
  await page.route("**/api/applications/today", (r) =>
    r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ submitted: 2, cap: 50 }) }));
});

test("home is the review inbox: failed rows are blocked, preparing rows are counted", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Review", level: 1 })).toBeVisible();
  await expect(page.getByText("Northwind").first()).toBeVisible();
  await expect(page.getByText("Contoso").first()).toBeVisible();
  await expect(page.getByText("Browser crashed while filling the form").first()).toBeVisible();
  await expect(page.getByLabel("Queue counts")).toContainText("1");
  await expect(page.getByText("Today: 2 of 50 submitted")).toBeVisible();
  await page.screenshot({ path: "tests/e2e/screenshots/24-review-home.png", fullPage: true });
});

test("the nav's Contacts link opens the contacts app and Review returns home", async ({ page }) => {
  await page.goto("/");
  await page.locator("nav").getByRole("link", { name: /^contacts$/i }).click();
  await expect(page).toHaveURL(/\/\?view=contacts$/);
  await expect(page.getByRole("heading", { name: "Cold Email Ops" })).toBeVisible();
  await page.locator("nav").getByRole("link", { name: /^review$/i }).click();
  await expect(page.getByRole("heading", { name: "Review", level: 1 })).toBeVisible();
});

import { test, expect, type Route } from "@playwright/test";
import { mockSupabase } from "./helpers";

// The one-tap approval queue at the top of /applications: progress toward ten, a ready card
// with its answers, Submit with a 5-second Undo, and a row that needs the operator.
const base = {
  contact_id: null, source: "jobright", source_channel: null, applied_date: null, notes: null,
  posting_snapshot: null, resume_file_ref: "r.pdf", cover_letter_file_ref: "c.pdf",
  resume_cost_usd: null, resume_tokens_input: null, resume_tokens_output: null, pick_reasoning: null,
  approved_at: null, approved_revision_hash: null, created_at: "2026-10-08T00:00:00Z", updated_at: "2026-10-08T00:00:00Z",
};
const queue = [
  { ...base, id: "41", company: "Northwind", role: "Product Manager, Payments", job_url: "https://jobs.lever.co/northwind/1",
    stage: "ready_to_submit", automation_status: "ready_for_review", pick_verdict: "strong", pick_score: 0.91,
    preview_revision_hash: "d".repeat(64), apply_blocked_reason: null,
    apply_preview: { platform: "lever", field_values: {},
      eligibility_answers: { "Are you legally authorized to work in the United States?": "Yes",
                             "Will you now or in the future require sponsorship?": "Yes" },
      screening_answers: { "Why Northwind?": "I have shipped payments onboarding for small businesses and want to do it at Northwind's scale." },
      keyword_coverage: { covered: ["SQL", "PRDs"], missing: ["Python"] } } },
  { ...base, id: "42", company: "Contoso", role: "Associate Product Manager", job_url: "https://contoso.wd5.myworkdayjobs.com/x",
    stage: "saved", automation_status: "needs_input", pick_verdict: "strong", pick_score: 0.8,
    preview_revision_hash: null, apply_preview: null,
    apply_blocked_reason: "Preview couldn't fill required questions: Application Questions: Desired salary" },
  { ...base, id: "43", company: "Fabrikam", role: "Product Manager", job_url: "https://boards.greenhouse.io/fabrikam/1",
    stage: "applied", automation_status: "submitted", pick_verdict: "strong", pick_score: 0.7,
    preview_revision_hash: null, apply_preview: null, apply_blocked_reason: null,
    submission_evidence: { source: "page_confirmation", url: "https://boards.greenhouse.io/fabrikam/confirmation" } },
];

async function routes(page: import("@playwright/test").Page, submits: string[]) {
  await mockSupabase(page);
  const list = async (route: Route) => {
    const url = route.request().url();
    const apps = url.includes("view=queue") ? queue : url.includes("takeover=open") ? [] : queue;
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ applications: apps }) });
  };
  await page.route("**/api/applications", list);
  await page.route("**/api/applications?*", list);
  await page.route("**/api/system-health", (r) =>
    r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ health: [] }) }));
  await page.route("**/api/applications/41/submit", async (r) => {
    submits.push(r.request().postData() ?? "");
    await r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ ok: true, queued: "beelink" }) });
  });
}

test.describe("Approval queue", () => {
  test("phone: review a card, submit with undo, see what needs you", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    const submits: string[] = [];
    await routes(page, submits);
    await page.goto("/applications");

    const queueSection = page.getByRole("region", { name: "Approval queue" });
    await expect(queueSection.getByRole("heading", { name: "1 of 10 submitted" })).toBeVisible({ timeout: 10_000 });
    const card = queueSection.getByRole("article", { name: "Northwind Product Manager, Payments" });
    await expect(card.getByText("Why Northwind?")).toBeVisible();
    await expect(queueSection.getByText("Preview couldn't fill required questions: Application Questions: Desired salary")).toBeVisible();
    // Nothing widens the page past the phone's width (the nav and the table scroll in place).
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
    await page.screenshot({ path: "tests/e2e/screenshots/21-queue-phone.png", fullPage: true });

    await card.getByRole("button", { name: "Submit" }).click();
    await expect(card.getByRole("status")).toContainText("Submitting in");
    await page.screenshot({ path: "tests/e2e/screenshots/21-queue-undo.png" });
    await card.getByRole("button", { name: "Undo" }).click();
    await page.waitForTimeout(6_000);
    expect(submits).toHaveLength(0);

    await card.getByRole("button", { name: "Submit" }).click();
    await expect.poll(() => submits.length, { timeout: 10_000 }).toBe(1);
    expect(JSON.parse(submits[0])).toEqual({ revision_hash: "d".repeat(64) });
  });

  test("desktop layout", async ({ page }) => {
    await routes(page, []);
    await page.goto("/applications");
    await expect(page.getByRole("heading", { name: "1 of 10 submitted" })).toBeVisible({ timeout: 10_000 });
    await page.screenshot({ path: "tests/e2e/screenshots/21-queue-desktop.png", fullPage: true });
  });
});

import { test, expect, type Route } from "@playwright/test";
import { mockSupabase } from "./helpers";

// A Beelink worker waiting on a CAPTCHA: the Applications page shows a "Needs you" card, and
// "I'm done" answers it through /api/applications/[id]/takeover-continue.
const waiting = {
  id: "7", contact_id: null, company: "Northwind", role: "Product Manager",
  job_url: "https://jobs.lever.co/northwind/1", source: "jobright", source_channel: null,
  stage: "ready_to_submit", applied_date: null, notes: null, posting_snapshot: null,
  resume_file_ref: "r.pdf", cover_letter_file_ref: "c.pdf", resume_cost_usd: null,
  resume_tokens_input: null, resume_tokens_output: null, pick_verdict: "strong", pick_score: 0.9,
  pick_reasoning: null, apply_preview: null, apply_blocked_reason: null, approved_at: null,
  automation_status: "preparing", preview_revision_hash: null, approved_revision_hash: null,
  worker_lease_id: "L1",
  takeover: { kind: "captcha", reason: "CAPTCHA after filling the form", lease: "L1",
              requested_at: "2026-10-08T03:00:00Z", continue_at: null },
  created_at: "2026-10-08T00:00:00Z", updated_at: "2026-10-08T03:00:00Z",
};

test.describe("Takeover card", () => {
  test("shows the waiting application and answers it with I'm done", async ({ page }) => {
    await mockSupabase(page);
    let answered = false;
    const list = async (route: Route) => {
      const open = route.request().url().includes("takeover=open");
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ applications: open && answered ? [] : [waiting] }),
      });
    };
    await page.route("**/api/applications", list);
    await page.route("**/api/applications?*", list);
    await page.route("**/api/system-health", (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ health: [] }) }));
    await page.route("**/api/applications/7/takeover-continue", async (route) => {
      answered = true;
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ ok: true }) });
    });

    await page.goto("/applications");
    const card = page.getByRole("region", { name: "Needs you" });
    await expect(card.getByText("Needs you: Northwind · Product Manager")).toBeVisible({ timeout: 10_000 });
    await expect(card.getByText("CAPTCHA after filling the form")).toBeVisible();
    await page.screenshot({ path: "tests/e2e/screenshots/20-takeover-card.png", fullPage: true });

    await card.getByRole("button", { name: "I'm done" }).click();
    await expect(card).toHaveCount(0, { timeout: 10_000 });
  });
});

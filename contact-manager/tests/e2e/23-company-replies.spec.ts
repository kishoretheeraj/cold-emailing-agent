import { test, expect, type Route } from "@playwright/test";
import { mockSupabase } from "./helpers";

// Employer emails read from the application inbox: an interview invite and a rejection appear under
// "Replies from companies", and Undo puts a wrongly read rejection back.
const base = {
  contact_id: null, source: "simplify", source_channel: "greenhouse", notes: null, posting_snapshot: null,
  resume_file_ref: "r.pdf", cover_letter_file_ref: "c.pdf", resume_cost_usd: null, resume_tokens_input: null,
  resume_tokens_output: null, pick_reasoning: null, approved_at: null, approved_revision_hash: null,
  created_at: "2026-10-01T00:00:00Z", updated_at: "2026-10-18T00:00:00Z", automation_status: "submitted",
  pick_verdict: "strong", pick_score: 0.8, preview_revision_hash: null, apply_preview: null, apply_blocked_reason: null,
  applied_date: "2026-10-10",
};

test("replies from companies: interview invite, rejection, undo", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await mockSupabase(page);
  let betaStage = "rejected";
  const patches: string[] = [];
  const rows = () => [
    { ...base, id: "61", company: "Northwind", role: "Associate Product Manager", stage: "phone_screen",
      outcome_evidence: { kind: "interview", message_id: "<a@x>", from: "Northwind <talent@northwind.com>",
        subject: "Next steps: phone screen with Northwind", date: "2026-10-18T14:00:00Z", previous_stage: "applied" } },
    { ...base, id: "62", company: "Contoso", role: "Product Analyst", stage: betaStage,
      outcome_evidence: { kind: "rejection", message_id: "<b@x>", from: "Contoso <no-reply@contoso.com>",
        subject: "Your application to Contoso", date: "2026-10-17T14:00:00Z", previous_stage: "applied" } },
  ];
  const list = async (route: Route) => {
    const url = route.request().url();
    const apps = url.includes("view=outcomes") ? rows() : [];
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ applications: apps }) });
  };
  await page.route("**/api/applications", list);
  await page.route("**/api/applications?*", list);
  await page.route("**/api/applications/today", (r) =>
    r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ submitted: 3, cap: 50 }) }));
  await page.route("**/api/system-health", (r) =>
    r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ health: [] }) }));
  await page.route("**/api/applications/62", async (r) => {
    patches.push(r.request().postData() ?? "");
    betaStage = "applied";
    await r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ application: { id: "62" } }) });
  });

  await page.goto("/applications");
  const queue = page.getByRole("region", { name: "Approval queue" });
  await expect(queue.getByRole("heading", { name: "Replies from companies" })).toBeVisible({ timeout: 10_000 });
  const northwind = queue.getByRole("article", { name: "Northwind reply" });
  await expect(northwind.getByText("interview invite")).toBeVisible();
  await expect(northwind.getByText(/Next steps: phone screen with Northwind/)).toBeVisible();
  const contoso = queue.getByRole("article", { name: "Contoso reply" });
  await expect(contoso.getByText("not moving forward")).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
  await page.screenshot({ path: "tests/e2e/screenshots/23-company-replies.png" });

  await contoso.getByRole("button", { name: "Not right? Undo" }).click();
  await expect.poll(() => patches.length).toBe(1);
  expect(JSON.parse(patches[0])).toEqual({ stage: "applied" });
  await expect(contoso.getByText("Undone")).toBeVisible();
});

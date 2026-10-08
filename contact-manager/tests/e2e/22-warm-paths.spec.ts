import { test, expect, type Route } from "@playwright/test";
import { mockSupabase } from "./helpers";

// Warm paths on the approval queue: who you know at the company, "Ask for a referral first" (the
// card stays visible with a badge and goes last), and the People panel: link a known contact,
// add a hiring manager, and an alum whose hook is only filled when tapped.
const base = {
  contact_id: null, source: "jobright", source_channel: null, applied_date: null, notes: null,
  posting_snapshot: null, resume_file_ref: "r.pdf", cover_letter_file_ref: "c.pdf",
  resume_cost_usd: null, resume_tokens_input: null, resume_tokens_output: null, pick_reasoning: null,
  approved_at: null, approved_revision_hash: null, created_at: "2026-10-08T00:00:00Z", updated_at: "2026-10-08T00:00:00Z",
  stage: "ready_to_submit", automation_status: "ready_for_review", pick_verdict: "strong",
  preview_revision_hash: "e".repeat(64), apply_blocked_reason: null,
  apply_preview: { platform: "greenhouse", field_values: {}, eligibility_answers: {},
    screening_answers: { "Why us?": "I have shipped lending products end to end." } },
};

function queueRows(held: boolean) {
  return [
    { ...base, id: "51", company: "Northwind", role: "Associate Product Manager", job_url: "https://boards.greenhouse.io/northwind/jobs/1",
      pick_score: 0.95, people: { linked: held ? 1 : 0, known: 2 },
      referral_hold_until: held ? new Date(Date.now() + 10 * 86_400_000).toISOString() : null },
    { ...base, id: "52", company: "Contoso", role: "Product Analyst", job_url: "https://jobs.lever.co/contoso/1",
      pick_score: 0.6, people: { linked: 0, known: 0 }, referral_hold_until: null },
  ];
}

function people(linked: unknown[]) {
  return {
    application: { company: "Northwind", role: "Associate Product Manager" },
    linked,
    known: [
      { id: 61, name: "Ann Lee", role: "Senior PM", mode: "networking", stage: "new", reply_status: "no_reply", blocker: null },
      { id: 62, name: "Bo Chan", role: "Recruiter", mode: "outreach", stage: "first_touch_sent", reply_status: "no_reply", blocker: "Already in touch" },
    ],
    known_emails: [{ name: "Ann Lee", email: "ann.lee@northwind.com" }, { name: "Bo Chan", email: "bo.chan@northwind.com" }],
    search_links: [
      { label: "LinkedIn: Dartmouth alumni", url: "https://www.linkedin.com/search/results/people/?keywords=Northwind%20Dartmouth" },
      { label: "Google: Dartmouth alumni", url: "https://www.google.com/search?q=x" },
    ],
    posting_emails: [{ email: "careers@northwind.com", kind: "inbox" }],
    remaining: 3 - linked.length, company_recent: linked.length, company_cap: 5, closed: false,
  };
}

test.describe("Warm paths", () => {
  test("hold for a referral, link a known contact, add a hiring manager and an alum", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await mockSupabase(page);
    let held = false;
    const linked: { id: number; name: string; mode: string; stage: string; reply_status: string; relationship: string }[] = [];
    const added: unknown[] = [];
    const list = async (route: Route) => {
      const url = route.request().url();
      const apps = url.includes("view=queue") ? queueRows(held) : url.includes("takeover=open") ? [] : queueRows(held);
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ applications: apps }) });
    };
    await page.route("**/api/applications", list);
    await page.route("**/api/applications?*", list);
    await page.route("**/api/applications/today", (r) =>
      r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ submitted: 4, cap: 50 }) }));
    await page.route("**/api/system-health", (r) =>
      r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ health: [] }) }));
    await page.route("**/api/applications/51/hold", async (r) => {
      held = r.request().method() === "POST";
      await r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ ok: true }) });
    });
    await page.route("**/api/applications/51/people", async (r) => {
      if (r.request().method() === "POST") {
        const body = JSON.parse(r.request().postData() ?? "{}");
        added.push(body);
        linked.push({ id: 70 + added.length, name: body.name, mode: body.relationship === "alum" ? "networking" : "applied",
          stage: "new", reply_status: "no_reply", relationship: body.relationship });
        await r.fulfill({ status: 201, contentType: "application/json", body: JSON.stringify({ contact: { id: 70 } }) });
        return;
      }
      await r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(people(linked)) });
    });
    await page.route("**/api/applications/51/people/link", async (r) => {
      linked.push({ id: 61, name: "Ann Lee", mode: "networking", stage: "new", reply_status: "no_reply", relationship: "team_member" });
      await r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ contact: { id: 61 } }) });
    });

    await page.goto("/applications");
    const queue = page.getByRole("region", { name: "Approval queue" });
    const card = queue.getByRole("article", { name: "Northwind Associate Product Manager" });
    await expect(card.getByText("You know 2 people here")).toBeVisible({ timeout: 10_000 });

    await card.getByRole("button", { name: "Ask for a referral first" }).click();
    const panel = card.getByRole("region", { name: "People" });
    await expect(panel.getByText("Linked 0 of 3", { exact: false })).toBeVisible();
    await expect(queue.getByRole("article").last().getByRole("note")).toContainText("Waiting on a referral until");
    await expect(queue.getByRole("article").first()).toContainText("Contoso");

    await panel.getByRole("button", { name: "Link" }).click();
    await expect(panel.getByText("Linked 1 of 3", { exact: false })).toBeVisible();
    await expect(panel.getByText("Already in touch")).toBeVisible();

    await panel.getByLabel("Name").fill("Jane Doe");
    await expect(panel.getByRole("button", { name: /Use jane.doe@northwind.com/ })).toBeVisible();
    await panel.getByRole("button", { name: /Use jane.doe@northwind.com/ }).click();
    await panel.getByLabel("Their title").fill("Director of Product");
    await panel.getByRole("button", { name: "Add person" }).click();
    await expect(panel.getByText("Linked 2 of 3", { exact: false })).toBeVisible();
    expect(added[0]).toMatchObject({ name: "Jane Doe", email: "jane.doe@northwind.com", relationship: "hiring_manager" });

    await panel.getByLabel("Relationship").selectOption("alum");
    await panel.getByLabel("School").selectOption("thayer");
    await expect(panel.getByLabel("Connection")).toHaveValue("");
    await panel.getByRole("button", { name: "Use suggestion" }).click();
    await expect(panel.getByLabel("Connection")).toHaveValue("Fellow Dartmouth Thayer alum");
    await panel.getByLabel("Name").fill("Al Um");
    await panel.getByLabel("Email").fill("al.um@northwind.com");
    await page.screenshot({ path: "tests/e2e/screenshots/22-warm-paths-phone.png", fullPage: true });
    await panel.getByRole("button", { name: "Add person" }).click();
    await expect(panel.getByText("Linked 3 of 3", { exact: false })).toBeVisible();
    expect(added[1]).toMatchObject({ relationship: "alum", school: "thayer", connection_context: "Fellow Dartmouth Thayer alum" });
    await expect(panel.getByRole("button", { name: "Add person" })).toBeDisabled();
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);

    await queue.getByRole("button", { name: "Stop waiting" }).click();
    await expect(queue.getByRole("note")).toHaveCount(0);
    await expect(queue.getByRole("article").first()).toContainText("Northwind");
  });
});

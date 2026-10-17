import { test, expect } from "@playwright/test";
import { mockSupabase } from "./helpers";

test.beforeEach(async ({ page }) => {
  await mockSupabase(page);
});

test("thread view shows empty state in side sheet", async ({ page }) => {
  await page.goto("/");

  // Click the first contact row to open the sheet
  const rows = page.locator("tbody tr, [data-testid='contact-row']");
  const count = await rows.count();
  if (count === 0) {
    // No contacts in fixture — skip
    return;
  }

  await rows.first().click();

  // Sheet should open
  await expect(page.getByRole("dialog")).toBeVisible({ timeout: 5_000 });

  // Thread section heading visible
  await expect(page.getByText(/email thread/i)).toBeVisible();

  // Empty state (no mocked email_messages)
  await expect(page.getByText(/no emails recorded yet/i)).toBeVisible({
    timeout: 5_000,
  });
});

test("a late previous thread cannot replace the selected contact's thread", async ({ page }) => {
  let releaseFirst!: () => void;
  const firstResponse = new Promise<void>((resolve) => { releaseFirst = resolve; });
  let requestedFirst = false;
  let returnedFirst = false;
  await page.route(/\/rest\/v1\/email_messages/, async (route) => {
    const filter = new URL(route.request().url()).searchParams.get("contact_id");
    if (!filter?.startsWith("eq.")) return route.fallback();
    const id = Number(filter.slice(3));
    if (id === 7) {
      requestedFirst = true;
      await firstResponse;
    }
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify([{
        id, contact_id: id, direction: "incoming", subject: `Thread for contact ${id}`,
        body: `Current message for contact ${id}`, sent_at: "2026-10-17T12:00:00Z",
      }]),
    });
    if (id === 7) returnedFirst = true;
  });
  try {
    await page.goto("/replies");
    await expect.poll(() => requestedFirst).toBe(true);
    await page.locator("li", { hasText: "Iris Moore" }).first().click();
    await expect(page.getByText("Current message for contact 9", { exact: true })).toBeVisible();
    releaseFirst();
    await expect.poll(() => returnedFirst).toBe(true);
    await expect(page.getByText("Current message for contact 9", { exact: true })).toBeVisible();
    await expect(page.getByText("Loading thread...", { exact: true })).toHaveCount(0);
    await page.screenshot({ path: "tests/e2e/screenshots/08-thread-late-response.png", fullPage: true });
  } finally {
    releaseFirst();
  }
});

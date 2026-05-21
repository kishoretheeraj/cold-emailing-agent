import { test, expect } from "@playwright/test";
import { mockSupabase } from "./helpers";

test.beforeEach(async ({ page }) => {
  await mockSupabase(page);
});

test("/replies page loads with two-column layout", async ({ page }) => {
  await page.goto("/replies");

  // Left rail: NEEDS RESPONSE header
  await expect(
    page.getByText(/needs response/i).first()
  ).toBeVisible({ timeout: 8_000 });

  // Grace Lee (positive_reply) appears in triage list
  await expect(
    page.locator("li", { hasText: "Grace Lee" }).first()
  ).toBeVisible({ timeout: 8_000 });

  // Right column: focused detail heading for Grace (first contact)
  await expect(
    page.getByRole("heading", { name: "Grace Lee" })
  ).toBeVisible({ timeout: 8_000 });
});

test("positive_reply contact shows suggested reply block", async ({ page }) => {
  await page.goto("/replies");
  await expect(
    page.getByRole("heading", { name: "Grace Lee" })
  ).toBeVisible({ timeout: 8_000 });

  // Suggested reply block visible (Approve and Send only renders when draft exists)
  await expect(page.getByText(/suggested reply/i)).toBeVisible({ timeout: 5_000 });
  await expect(
    page.getByRole("button", { name: /approve and send/i })
  ).toBeVisible({ timeout: 3_000 });
});

test("hard_no contact shows no-draft explanation", async ({ page }) => {
  await page.goto("/replies");
  await expect(
    page.getByRole("heading", { name: "Grace Lee" })
  ).toBeVisible({ timeout: 8_000 });

  // Click Quinn Thompson (hard_no)
  await page.locator("li", { hasText: "Quinn Thompson" }).first().click();
  await expect(
    page.getByRole("heading", { name: "Quinn Thompson" })
  ).toBeVisible({ timeout: 5_000 });

  await expect(
    page.getByText(/no suggested reply drafted/i)
  ).toBeVisible({ timeout: 3_000 });
  await expect(
    page.getByText(/agent only drafts replies for positive/i)
  ).toBeVisible({ timeout: 3_000 });
});

test("clicking a row updates the right column", async ({ page }) => {
  await page.goto("/replies");
  await expect(
    page.getByRole("heading", { name: "Grace Lee" })
  ).toBeVisible({ timeout: 8_000 });

  await page.locator("li", { hasText: "Iris Moore" }).first().click();
  await expect(
    page.getByRole("heading", { name: "Iris Moore" })
  ).toBeVisible({ timeout: 5_000 });
});

test("j/k keyboard navigation changes focused row", async ({ page }) => {
  await page.goto("/replies");
  await expect(
    page.getByRole("heading", { name: "Grace Lee" })
  ).toBeVisible({ timeout: 8_000 });

  await page.keyboard.press("j");
  await expect(
    page.getByRole("heading", { name: "Iris Moore" })
  ).toBeVisible({ timeout: 5_000 });

  await page.keyboard.press("k");
  await expect(
    page.getByRole("heading", { name: "Grace Lee" })
  ).toBeVisible({ timeout: 5_000 });
});

test("e key shows undo toast; clicking Undo cancels send", async ({ page }) => {
  await page.goto("/replies");
  await expect(
    page.getByRole("heading", { name: "Grace Lee" })
  ).toBeVisible({ timeout: 8_000 });

  await page.keyboard.press("e");

  // Undo toast appears
  await expect(page.getByText(/sending to grace/i)).toBeVisible({ timeout: 5_000 });

  // Click Undo
  await page.getByRole("button", { name: /undo/i }).click();

  await expect(page.getByText(/canceled/i)).toBeVisible({ timeout: 3_000 });
});

test("e key send: after 6s, POST /api/send-draft fires", async ({ page }) => {
  const sendRequests: string[] = [];
  page.on("request", (req) => {
    if (req.url().includes("/api/send-draft")) {
      sendRequests.push(req.method());
    }
  });

  await page.goto("/replies");
  await expect(
    page.getByRole("heading", { name: "Grace Lee" })
  ).toBeVisible({ timeout: 8_000 });

  await page.keyboard.press("e");
  await expect(page.getByText(/sending to grace/i)).toBeVisible({ timeout: 5_000 });

  await page.waitForTimeout(6_000);

  expect(sendRequests.length).toBeGreaterThan(0);
});

test("E key opens Quick Fix; Cancel returns to read-only", async ({ page }) => {
  await page.goto("/replies");
  await expect(
    page.getByRole("heading", { name: "Grace Lee" })
  ).toBeVisible({ timeout: 8_000 });

  await page.keyboard.press("E");

  await expect(
    page.getByRole("button", { name: /save and send/i })
  ).toBeVisible({ timeout: 5_000 });
  await expect(
    page.getByRole("button", { name: /cancel/i })
  ).toBeVisible({ timeout: 3_000 });

  await page.getByRole("button", { name: /cancel/i }).click();

  await expect(
    page.getByRole("button", { name: /approve and send/i })
  ).toBeVisible({ timeout: 3_000 });
  expect(await page.locator("textarea").count()).toBe(0);
});

test("Quick Fix: Save and Send calls /api/update-draft then undo toast", async ({
  page,
}) => {
  const updateRequests: string[] = [];
  page.on("request", (req) => {
    if (req.url().includes("/api/update-draft")) {
      updateRequests.push(req.method());
    }
  });

  await page.goto("/replies");
  await expect(
    page.getByRole("heading", { name: "Grace Lee" })
  ).toBeVisible({ timeout: 8_000 });

  await page.keyboard.press("E");
  await expect(
    page.getByRole("button", { name: /save and send/i })
  ).toBeVisible({ timeout: 5_000 });

  const subjectInput = page.getByRole("textbox").first();
  await subjectInput.fill("Edited subject");

  await page.getByRole("button", { name: /save and send/i }).click();

  await page.waitForTimeout(1_000);
  expect(updateRequests.length).toBeGreaterThan(0);

  await expect(page.getByText(/sending to grace/i)).toBeVisible({ timeout: 5_000 });
});

test("i key: undo toast appears for mark interested", async ({ page }) => {
  const patchRequests: string[] = [];
  page.on("request", (req) => {
    if (req.url().includes("/rest/v1/contacts") && req.method() === "PATCH") {
      patchRequests.push(req.method());
    }
  });

  await page.goto("/replies");
  await expect(
    page.getByRole("heading", { name: "Grace Lee" })
  ).toBeVisible({ timeout: 8_000 });

  await page.keyboard.press("i");

  await expect(page.getByText(/interested/i).first()).toBeVisible({ timeout: 5_000 });
  // PATCH not called yet (5s delay)
  expect(patchRequests.length).toBe(0);
});

test("empty state when no contacts have classifier_status", async ({ page }) => {
  // Override contacts to return only contacts without classifier_status (stage filter)
  await page.route(/\/rest\/v1\/contacts\?.*classifier_status=not/, async (route) => {
    if (route.request().method() === "GET") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify([]),
        headers: { "Content-Range": "0-0/0" },
      });
    } else {
      await route.continue();
    }
  });

  await page.goto("/replies");
  await expect(page.getByText(/no replies to triage/i).first()).toBeVisible({
    timeout: 8_000,
  });
});

test("Replies nav link is present on home page", async ({ page }) => {
  await page.goto("/");
  const repliesLink = page.getByRole("link", { name: /^replies$/i });
  await expect(repliesLink).toBeVisible();
  await expect(repliesLink).toHaveAttribute("href", "/replies");
});

test("screenshot: replies page loaded", async ({ page }) => {
  await page.goto("/replies");
  await expect(
    page.getByRole("heading", { name: "Grace Lee" })
  ).toBeVisible({ timeout: 8_000 });
  await page.screenshot({
    path: "tests/e2e/screenshots/12-replies-loaded.png",
    fullPage: false,
  });
});

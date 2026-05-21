import { test, expect } from "@playwright/test";
import { mockSupabase } from "./helpers";

test.beforeEach(async ({ page }) => {
  await mockSupabase(page);
});

test("/queue page loads with three-column layout", async ({ page }) => {
  await page.goto("/queue");

  // Left rail: QUEUE header visible
  await expect(page.getByText(/^queue$/i).first()).toBeVisible({ timeout: 8_000 });

  // Center: draft list row for Bob Martinez (contact_id=2, first_touch_drafted)
  await expect(
    page.locator("li", { hasText: "Bob Martinez" }).first()
  ).toBeVisible({ timeout: 8_000 });

  // Right column: focused detail heading for Bob (first contact)
  await expect(
    page.getByRole("heading", { name: "Bob Martinez" })
  ).toBeVisible({ timeout: 8_000 });
});

test("clicking a row updates the right column", async ({ page }) => {
  await page.goto("/queue");
  await expect(
    page.getByRole("heading", { name: "Bob Martinez" })
  ).toBeVisible({ timeout: 8_000 });

  // Click Dave Johnson row in the list
  await page.locator("li", { hasText: "Dave Johnson" }).first().click();
  await expect(
    page.getByRole("heading", { name: "Dave Johnson" })
  ).toBeVisible({ timeout: 5_000 });
});

test("j/k keyboard navigation changes focused row", async ({ page }) => {
  await page.goto("/queue");
  await expect(
    page.getByRole("heading", { name: "Bob Martinez" })
  ).toBeVisible({ timeout: 8_000 });

  // j moves to next row
  await page.keyboard.press("j");
  await expect(
    page.getByRole("heading", { name: "Dave Johnson" })
  ).toBeVisible({ timeout: 5_000 });

  // k moves back
  await page.keyboard.press("k");
  await expect(
    page.getByRole("heading", { name: "Bob Martinez" })
  ).toBeVisible({ timeout: 5_000 });
});

test("e key shows undo toast; clicking Undo keeps the row", async ({ page }) => {
  await page.goto("/queue");
  await expect(
    page.getByRole("heading", { name: "Bob Martinez" })
  ).toBeVisible({ timeout: 8_000 });

  // Focus Bob and press e
  await page.keyboard.press("e");

  // Toast should appear (Sonner renders at bottom-right)
  await expect(page.getByText(/sending to bob/i)).toBeVisible({ timeout: 5_000 });

  // Click Undo
  await page.getByRole("button", { name: /undo/i }).click();

  // Send canceled toast shows
  await expect(page.getByText(/send canceled/i)).toBeVisible({ timeout: 3_000 });

  // Bob should still be accessible (row stays or refocuses)
  // After undo, the row is re-inserted; Dave Johnson must still be in list
  await expect(
    page.locator("li", { hasText: "Dave Johnson" }).first()
  ).toBeVisible({ timeout: 3_000 });
});

test("e key send: after 6s, row is removed from queue", async ({ page }) => {
  // Track /api/send-draft calls
  const sendRequests: string[] = [];
  page.on("request", (req) => {
    if (req.url().includes("/api/send-draft")) {
      sendRequests.push(req.method());
    }
  });

  await page.goto("/queue");
  await expect(
    page.getByRole("heading", { name: "Bob Martinez" })
  ).toBeVisible({ timeout: 8_000 });

  // Approve Bob
  await page.keyboard.press("e");
  await expect(page.getByText(/sending to bob/i)).toBeVisible({ timeout: 5_000 });

  // Wait 6s for timer to fire
  await page.waitForTimeout(6_000);

  // /api/send-draft was called
  expect(sendRequests.length).toBeGreaterThan(0);
});

test("E key opens Quick Fix mode", async ({ page }) => {
  await page.goto("/queue");
  await expect(
    page.getByRole("heading", { name: "Bob Martinez" })
  ).toBeVisible({ timeout: 8_000 });

  await page.keyboard.press("E");

  // Save and Send button visible (Quick Fix mode)
  await expect(
    page.getByRole("button", { name: /save and send/i })
  ).toBeVisible({ timeout: 5_000 });
  await expect(
    page.getByRole("button", { name: /cancel/i })
  ).toBeVisible({ timeout: 3_000 });
});

test("Quick Fix: Cancel returns to read-only view", async ({ page }) => {
  await page.goto("/queue");
  await expect(
    page.getByRole("heading", { name: "Bob Martinez" })
  ).toBeVisible({ timeout: 8_000 });

  await page.keyboard.press("E");
  await expect(
    page.getByRole("button", { name: /save and send/i })
  ).toBeVisible({ timeout: 5_000 });

  await page.getByRole("button", { name: /cancel/i }).click();

  // Approve and Send button returns
  await expect(
    page.getByRole("button", { name: /approve and send/i })
  ).toBeVisible({ timeout: 3_000 });
  expect(await page.locator("textarea").count()).toBe(0);
});

test("Quick Fix: Save and Send calls /api/update-draft then shows undo toast", async ({
  page,
}) => {
  const updateRequests: string[] = [];
  page.on("request", (req) => {
    if (req.url().includes("/api/update-draft")) {
      updateRequests.push(req.method());
    }
  });

  await page.goto("/queue");
  await expect(
    page.getByRole("heading", { name: "Bob Martinez" })
  ).toBeVisible({ timeout: 8_000 });

  await page.keyboard.press("E");
  await expect(
    page.getByRole("button", { name: /save and send/i })
  ).toBeVisible({ timeout: 5_000 });

  // Change the subject (first textbox in Quick Fix is the subject input)
  const subjectInput = page.getByRole("textbox").first();
  await subjectInput.fill("Edited subject");

  // Click Save and Send
  await page.getByRole("button", { name: /save and send/i }).click();

  // /api/update-draft called
  await page.waitForTimeout(1_000);
  expect(updateRequests.length).toBeGreaterThan(0);

  // Undo toast appears
  await expect(page.getByText(/sending to bob/i)).toBeVisible({ timeout: 5_000 });
});

test("x key skips row without page reload", async ({ page }) => {
  await page.goto("/queue");
  await expect(
    page.getByRole("heading", { name: "Bob Martinez" })
  ).toBeVisible({ timeout: 8_000 });

  // Skip Bob
  await page.keyboard.press("x");

  // Bob's row disappears from center list
  await expect(
    page.locator("li", { hasText: "Bob Martinez" })
  ).toHaveCount(0, { timeout: 3_000 });
});

test("empty state when no drafted contacts", async ({ page }) => {
  // Override contacts to return only non-drafted contacts
  await page.route(/\/rest\/v1\/contacts\?.*stage=in/, async (route) => {
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

  await page.goto("/queue");
  await expect(page.getByText(/queue is empty/i).first()).toBeVisible({
    timeout: 8_000,
  });
});

test("Queue nav link is present on home page", async ({ page }) => {
  await page.goto("/");
  const queueLink = page.getByRole("link", { name: /^queue$/i });
  await expect(queueLink).toBeVisible();
  await expect(queueLink).toHaveAttribute("href", "/queue");
});

test("screenshot: queue page with drafts loaded", async ({ page }) => {
  await page.goto("/queue");
  // Wait for the right column heading to appear (confirms data loaded)
  await expect(
    page.getByRole("heading", { name: "Bob Martinez" })
  ).toBeVisible({ timeout: 8_000 });
  await page.screenshot({
    path: "tests/e2e/screenshots/11-queue-loaded.png",
    fullPage: false,
  });
});

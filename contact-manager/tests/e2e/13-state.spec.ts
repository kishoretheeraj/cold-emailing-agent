import { test, expect } from "@playwright/test";
import { mockSupabase } from "./helpers";

test.beforeEach(async ({ page }) => {
  await mockSupabase(page);
});

test("queue row with state shows state code and local time", async ({ page }) => {
  await page.goto("/queue");
  await expect(
    page.getByRole("heading", { name: "Bob Martinez" })
  ).toBeVisible({ timeout: 8_000 });

  // Bob Martinez has state=NY — row should show "NY · HH:MM AM/PM"
  const bobRow = page.locator("li", { hasText: "Bob Martinez" }).first();
  await expect(bobRow).toContainText("NY");
  await expect(bobRow).toContainText(/NY · \d{1,2}:\d{2} [AP]M/);
});

test("queue row without state shows no location label", async ({ page }) => {
  await page.goto("/queue");
  await expect(
    page.getByRole("heading", { name: "Bob Martinez" })
  ).toBeVisible({ timeout: 8_000 });

  // Frank Brown has no state — row should not show a time label
  const frankRow = page.locator("li", { hasText: "Frank Brown" }).first();
  await expect(frankRow).toBeVisible({ timeout: 5_000 });
  await expect(frankRow).not.toContainText(/\d{1,2}:\d{2} [AP]M/);
});

test("queue header shows sender time and timezone distribution", async ({ page }) => {
  await page.goto("/queue");
  await expect(
    page.getByRole("heading", { name: "Bob Martinez" })
  ).toBeVisible({ timeout: 8_000 });

  // Header paragraph shows "Your time: HH:MM AM/PM <tz> · 2 ET"
  // Bob and Dave both have state=NY → ET, so distribution shows "2 ET"
  const header = page.locator("p", { hasText: /your time:/i }).first();
  await expect(header).toBeVisible({ timeout: 5_000 });
  await expect(header).toContainText(/\d{1,2}:\d{2} [AP]M/);
  await expect(header).toContainText("ET");
});

test("contacts side sheet state dropdown shows Unknown default", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByText("Alice Chen")).toBeVisible({ timeout: 10_000 });

  // Alice Chen has no state — click to open side sheet
  await page.getByRole("button", { name: /alice chen/i }).click();
  await expect(page.getByRole("dialog")).toBeVisible({ timeout: 5_000 });

  // State SelectTrigger should display the placeholder when state is null
  await expect(
    page.getByRole("dialog").getByText("Unknown / not US")
  ).toBeVisible({ timeout: 3_000 });
});

test("screenshot: queue page with state labels", async ({ page }) => {
  await page.goto("/queue");
  await expect(
    page.getByRole("heading", { name: "Bob Martinez" })
  ).toBeVisible({ timeout: 8_000 });
  await page.screenshot({
    path: "tests/e2e/screenshots/13-state-queue.png",
    fullPage: false,
  });
});

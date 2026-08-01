import { test, expect } from "@playwright/test";
import { mockSupabase } from "./helpers";

test.beforeEach(async ({ page }) => {
  await mockSupabase(page);
});

test("main page has expected chrome: title, nav links, input toggle", async ({
  page,
}) => {
  await page.goto("/");

  await expect(page.getByRole("heading", { name: "Cold Email Ops" })).toBeVisible();

  const overviewLink = page.getByRole("link", { name: /^overview$/i });
  await expect(overviewLink).toBeVisible();
  await expect(overviewLink).toHaveAttribute("href", "/overview");

  const promptsLink = page.getByRole("link", { name: /^prompts$/i });
  await expect(promptsLink).toBeVisible();
  await expect(promptsLink).toHaveAttribute("href", "/prompts");

  const queueLink = page.getByRole("link", { name: /^queue$/i });
  await expect(queueLink).toBeVisible();
  await expect(queueLink).toHaveAttribute("href", "/queue");

  const repliesLink = page.getByRole("link", { name: /^replies$/i });
  await expect(repliesLink).toBeVisible();
  await expect(repliesLink).toHaveAttribute("href", "/replies");

  const importLink = page.getByRole("link", { name: /^import$/i });
  await expect(importLink).toBeVisible();
  await expect(importLink).toHaveAttribute("href", "/import");

  const runsLink = page.getByRole("link", { name: /activity/i });
  await expect(runsLink).toBeVisible();
  await expect(runsLink).toHaveAttribute("href", "/runs");

  const labLink = page.getByRole("link", { name: /^lab$/i });
  await expect(labLink).toBeVisible();
  await expect(labLink).toHaveAttribute("href", "/lab");

  const visaLink = page.getByRole("link", { name: /^visa/i });
  await expect(visaLink).toBeVisible();
  await expect(visaLink).toHaveAttribute("href", "/visa-review");

  await expect(page.getByRole("button", { name: "Smart Input" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Structured Form" })).toBeVisible();
});

test("Prompts link navigates to /prompts", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("link", { name: /prompts/i }).click();
  await expect(page).toHaveURL(/\/prompts/);
  await expect(page.getByRole("heading", { name: /prompts/i })).toBeVisible({
    timeout: 10_000,
  });
});

test("nav bar is present on /queue (global layout)", async ({ page }) => {
  await mockSupabase(page);
  await page.goto("/queue");
  await expect(page.getByRole("link", { name: /^overview$/i })).toBeVisible();
  await expect(page.getByRole("link", { name: /^replies$/i })).toBeVisible();
  await expect(page.getByRole("button", { name: /run agent/i })).toBeVisible();
});

test("Pause Agent button is visible in the nav", async ({ page }) => {
  await page.route("**/api/agent-config", (route) =>
    route.fulfill({ json: { scope: "none" } })
  );
  await page.goto("/");
  await expect(page.getByRole("button", { name: /pause agent/i })).toBeVisible({
    timeout: 5000,
  });
});

test("Run Agent opens confirmation modal", async ({ page }) => {
  await page.route("**/api/agent-config", (route) =>
    route.fulfill({ json: { scope: "none" } })
  );
  await page.goto("/");
  await page.getByRole("button", { name: /pause agent/i }).waitFor({ timeout: 5000 });
  await page.getByRole("button", { name: /run agent/i }).click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await expect(page.getByText(/trigger agent now/i)).toBeVisible();
  const screenshot = await page.screenshot();
  expect(screenshot).toBeTruthy();
});

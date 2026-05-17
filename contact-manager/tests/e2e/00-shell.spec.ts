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

  const promptsLink = page.getByRole("link", { name: /prompts/i });
  await expect(promptsLink).toBeVisible();
  await expect(promptsLink).toHaveAttribute("href", "/prompts");

  const runsLink = page.getByRole("link", { name: /activity/i });
  await expect(runsLink).toBeVisible();
  await expect(runsLink).toHaveAttribute("href", "/runs");

  await expect(page.getByRole("button", { name: "Smart Input" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Structured Form" })).toBeVisible();
});

test("Prompts & Profile link navigates to /prompts", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("link", { name: /prompts/i }).click();
  await expect(page).toHaveURL(/\/prompts/);
  await expect(page.getByRole("heading", { name: /prompts/i })).toBeVisible({
    timeout: 10_000,
  });
});

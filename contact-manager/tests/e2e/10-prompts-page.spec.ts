import { test, expect } from "@playwright/test";
import { mockSupabase } from "./helpers";

// The fixture (tests/e2e/fixtures/prompts.json) has 13 rows that map to these categories:
//   Sender & Core        — sender_profile, outreach_prompt, critic_prompt, subject_prompt (4)
//   Outreach Modifiers   — 7 instruction rows
//   Applied              — applied_intro_prompt, applied_followup_prompt (2)
//   Research Pipeline    — not in fixture → hidden
//   Reply Pipeline       — not in fixture → hidden
//   Retrospective        — not in fixture → hidden
//   Shared               — dartmouth_instruction (1)

test.beforeEach(async ({ page }) => {
  await mockSupabase(page);
  await page.goto("/prompts");
  // Wait for prompts to load (search input appears once data is ready)
  await expect(page.getByPlaceholder("Search prompts...")).toBeVisible({ timeout: 10_000 });
});

test("/prompts shows 4 category sections from fixture data", async ({ page }) => {
  const categories = page.locator("[data-testid='category-header']");
  await expect(categories).toHaveCount(4);

  // Category names should appear in the fixed CATEGORY_ORDER
  await expect(page.getByText("Sender & Core")).toBeVisible();
  await expect(page.getByText("Outreach Modifiers")).toBeVisible();
  await expect(page.getByText("Applied")).toBeVisible();
  await expect(page.getByText("Shared")).toBeVisible();

  // Empty categories should not render
  await expect(page.getByText("Research Pipeline")).not.toBeVisible();
  await expect(page.getByText("Reply Pipeline")).not.toBeVisible();
  await expect(page.getByText("Retrospective")).not.toBeVisible();
});

test("/prompts default state: only Sender & Core is expanded", async ({ page }) => {
  // "Sender & Core" header button should have aria-expanded=true
  const senderHeader = page.getByRole("button", { name: /Sender & Core/ });
  await expect(senderHeader).toHaveAttribute("aria-expanded", "true");

  // Other categories should be collapsed
  const outreachHeader = page.getByRole("button", { name: /Outreach Modifiers/ });
  await expect(outreachHeader).toHaveAttribute("aria-expanded", "false");

  const appliedHeader = page.getByRole("button", { name: /Applied/ });
  await expect(appliedHeader).toHaveAttribute("aria-expanded", "false");
});

test("/prompts clicking a collapsed category expands it", async ({ page }) => {
  const outreachHeader = page.getByRole("button", { name: /Outreach Modifiers/ });

  // Starts collapsed
  await expect(outreachHeader).toHaveAttribute("aria-expanded", "false");

  // Click to open
  await outreachHeader.click();
  await expect(outreachHeader).toHaveAttribute("aria-expanded", "true");

  // The prompt cards inside should now be visible (look for one of the display_title values)
  await expect(page.getByText("Outreach: First Touch Instruction")).toBeVisible();
});

test("/prompts clicking an open category collapses it", async ({ page }) => {
  // "Sender & Core" is open by default
  const senderHeader = page.getByRole("button", { name: /Sender & Core/ });
  await expect(senderHeader).toHaveAttribute("aria-expanded", "true");

  await senderHeader.click();
  await expect(senderHeader).toHaveAttribute("aria-expanded", "false");
});

test("/prompts search filters to matching categories only", async ({ page }) => {
  const searchInput = page.getByPlaceholder("Search prompts...");

  // "first touch" matches "Outreach: First Touch Instruction" (Outreach Modifiers only).
  // Note: "tier" is avoided because critic_prompt's description mentions "Tier 1".
  await searchInput.fill("first touch");

  // Outreach Modifiers should be visible
  await expect(page.getByText("Outreach Modifiers")).toBeVisible();

  // Applied has no "first touch" in titles/descriptions — should be hidden
  await expect(page.getByText("Applied")).not.toBeVisible();
  // Shared has no match either
  await expect(page.getByText("Shared")).not.toBeVisible();
});

test("/prompts clearing search restores all categories", async ({ page }) => {
  const searchInput = page.getByPlaceholder("Search prompts...");
  await searchInput.fill("first touch");

  // Confirm filtered state (Applied is hidden)
  await expect(page.getByText("Applied")).not.toBeVisible();

  // Clear the search
  await searchInput.fill("");

  // All categories with fixture data should be visible again
  await expect(page.getByText("Sender & Core")).toBeVisible();
  await expect(page.getByText("Outreach Modifiers")).toBeVisible();
  await expect(page.getByText("Applied")).toBeVisible();
});

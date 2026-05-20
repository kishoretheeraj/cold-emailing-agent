import { test, expect } from "@playwright/test";
import { mockSupabase } from "./helpers";
import path from "node:path";

const SCREENSHOTS = path.join(__dirname, "screenshots");

test.beforeEach(async ({ page }) => {
  await mockSupabase(page);
});

test("outreach contact: stage dropdown shows only Outreach section with descriptive labels", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.goto("/");
  await expect(page.getByText("Alice Chen")).toBeVisible({ timeout: 10_000 });

  // Open side sheet for Alice (outreach contact)
  await page.getByRole("button", { name: /alice chen/i }).click();
  await expect(page.getByRole("dialog")).toBeVisible({ timeout: 5_000 });

  // Open the stage dropdown
  const combobox = page.getByRole("combobox").first();
  await combobox.click();
  await page.waitForTimeout(300);

  // Assert descriptive labels are present
  await expect(page.getByRole("option", { name: "First Touch Draft" })).toBeVisible();
  await expect(page.getByRole("option", { name: "Followup 1 Draft" })).toBeVisible();
  await expect(page.getByRole("option", { name: "Breakup Draft" })).toBeVisible();

  // Assert old generic labels are gone
  await expect(page.getByRole("option", { name: "Draft 1" })).not.toBeVisible();
  await expect(page.getByRole("option", { name: "Draft 2" })).not.toBeVisible();

  // Assert Applied section is NOT shown for an outreach contact
  await expect(page.getByRole("option", { name: "App Intro Draft" })).not.toBeVisible();
  await expect(page.getByRole("option", { name: "App Followup Draft" })).not.toBeVisible();

  await page.screenshot({
    path: path.join(SCREENSHOTS, "stage-dropdown-outreach.png"),
  });
});

test("applied contact: stage dropdown shows only Applied section with descriptive labels", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.goto("/");
  await expect(page.getByText("Alice Chen")).toBeVisible({ timeout: 10_000 });

  // Search for Emily to bring her into view (she's beyond the first page)
  const searchInput = page.getByPlaceholder(/search/i);
  await searchInput.fill("Emily");
  await page.waitForTimeout(500);
  await expect(page.getByText("Emily Scott")).toBeVisible({ timeout: 5_000 });

  // Set up response waiter BEFORE clicking to capture the async full-record fetch.
  // openContact() calls setSelectedContact(listData) immediately then fetches select("*")
  // which triggers a re-render. We must wait for that re-render before opening the dropdown.
  const fullRecordFetch = page.waitForResponse(
    (r) =>
      r.url().includes("/rest/v1/contacts") &&
      (r.request().headers()["accept"] ?? "").includes("pgrst.object")
  );

  // Open side sheet for Emily (applied contact)
  await page.getByRole("button", { name: /emily scott/i }).click();
  await expect(page.getByRole("dialog")).toBeVisible({ timeout: 5_000 });
  await fullRecordFetch;

  // Open the stage dropdown
  const combobox = page.getByRole("combobox").first();
  await combobox.click();
  await page.waitForTimeout(300);

  // Assert Applied labels are present
  await expect(page.getByRole("option", { name: "App Intro Draft" })).toBeVisible();
  await expect(page.getByRole("option", { name: "App Followup Draft" })).toBeVisible();

  // Assert Outreach stages are NOT shown for an applied contact
  await expect(page.getByRole("option", { name: "First Touch Draft" })).not.toBeVisible();
  await expect(page.getByRole("option", { name: "Breakup Draft" })).not.toBeVisible();

  await page.screenshot({
    path: path.join(SCREENSHOTS, "stage-dropdown-applied.png"),
  });
});

import { test, expect } from "@playwright/test";
import { mockSupabase } from "./helpers";

const sampleContacts = JSON.stringify([
  {
    name: "Sonali Aggarwal",
    email: "sonali_a02@yahoo.com",
    company: "Workiva",
    role: "Director of Product Marketing",
    notes: "Function: Marketing - Brand/Product Management | Industry: Other/Not Available",
    dartmouth: true,
    mode: "outreach",
    tier: 2,
  },
  {
    name: "Segun Adetayo",
    email: "adetayooluwasegun88@gmail.com",
    company: "Microsoft Corporation",
    role: "Global Product Marketing Manager",
    notes: "Function: Marketing - Brand/Product Management | Industry: Technology - IT and Services",
    dartmouth: true,
    mode: "outreach",
    tier: 2,
  },
]);

test.beforeEach(async ({ page }) => {
  await mockSupabase(page);
});

test("/import page loads with heading and disabled button", async ({ page }) => {
  await page.goto("/import");

  await expect(page.getByRole("heading", { name: /import contacts/i })).toBeVisible();
  await expect(page.getByRole("textbox")).toBeVisible();
  await expect(page.getByRole("button", { name: /review contacts/i })).toBeDisabled();

  // Nav import link is active
  const importLink = page.getByRole("link", { name: /^import$/i });
  await expect(importLink).toBeVisible();

  await page.screenshot({ path: "tests/e2e/screenshots/14-import-empty.png" });
});

test("/import — pasting valid JSON enables button and enters review phase", async ({
  page,
}) => {
  await page.goto("/import");

  const textarea = page.getByRole("textbox");
  await textarea.fill(sampleContacts);

  const btn = page.getByRole("button", { name: /review contacts/i });
  await expect(btn).toBeEnabled();
  await btn.click();

  // ReviewFlow should now be visible — look for navigation buttons it renders
  await expect(page.getByRole("button", { name: /skip/i }).first()).toBeVisible({
    timeout: 5_000,
  });

  await page.screenshot({ path: "tests/e2e/screenshots/14-import-review.png" });
});

test("/import review — company and role fields are visible and editable", async ({
  page,
}) => {
  await page.goto("/import");
  await page.getByRole("textbox").fill(sampleContacts);
  await page.getByRole("button", { name: /review contacts/i }).click();

  await expect(page.getByRole("button", { name: /skip/i }).first()).toBeVisible({
    timeout: 5_000,
  });

  // Company field pre-filled and editable
  const companyInput = page.getByPlaceholder("Company name");
  await expect(companyInput).toBeVisible();
  await expect(companyInput).toHaveValue("Workiva");
  await companyInput.fill("Workiva Inc");
  await expect(companyInput).toHaveValue("Workiva Inc");

  // Role field pre-filled
  const roleInput = page.getByPlaceholder("Job title");
  await expect(roleInput).toBeVisible();
  await expect(roleInput).toHaveValue("Director of Product Marketing");

  await page.screenshot({ path: "tests/e2e/screenshots/14-import-editable-fields.png" });
});

test("/import review — Search web link is visible with correct href", async ({ page }) => {
  await page.goto("/import");
  await page.getByRole("textbox").fill(sampleContacts);
  await page.getByRole("button", { name: /review contacts/i }).click();
  await expect(page.getByRole("button", { name: /skip/i }).first()).toBeVisible({
    timeout: 5_000,
  });

  const searchLink = page.getByRole("link", { name: /search web/i });
  await expect(searchLink).toBeVisible();
  const href = await searchLink.getAttribute("href");
  expect(href).toContain("google.com/search");
  expect(href).toContain("Sonali");
  expect(href).toContain("Workiva");
});

test("/import — non-array JSON keeps button disabled", async ({ page }) => {
  await page.goto("/import");
  // Object (not array) starts with { so the button stays disabled
  await page.getByRole("textbox").fill('{"name": "test"}');
  await expect(page.getByRole("button", { name: /review contacts/i })).toBeDisabled();
});

import { test, expect } from "@playwright/test";

// The login form talks only to /api/login, so it is mocked here; the session itself is covered by
// operatorAuth/proxy unit tests (the dev server runs without OPERATOR_PASSWORD).
test.describe("Login page", () => {
  test("shows the form without the app nav and reports a wrong password", async ({ page }) => {
    await page.route("**/api/login", (route) =>
      route.fulfill({ status: 401, contentType: "application/json", body: JSON.stringify({ error: "Wrong password" }) })
    );
    await page.goto("/login?next=/applications");
    await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
    await expect(page.getByRole("link", { name: /^applications$/i })).toHaveCount(0);
    const submit = page.getByRole("button", { name: "Sign in" });
    await expect(submit).toBeDisabled();

    await page.getByLabel("Password").fill("wrong password");
    await submit.click();
    await expect(page.getByRole("alert").filter({ hasText: "Wrong password." })).toBeVisible();
    await page.screenshot({ path: "tests/e2e/screenshots/19-login-wrong-password.png", fullPage: true });
  });

  test("goes to the requested page after a successful sign-in", async ({ page }) => {
    await page.route("**/api/login", (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ ok: true }) })
    );
    await page.goto("/login?next=/runs");
    await page.getByLabel("Password").fill("correct horse battery");
    await page.getByRole("button", { name: "Sign in" }).click();
    await expect(page).toHaveURL(/\/runs$/);
  });
});

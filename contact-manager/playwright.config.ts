import { defineConfig } from "@playwright/test";
import { createSessionToken, SESSION_COOKIE } from "./src/lib/operatorAuth";

// E2E_PORT lets a second checkout (a git worktree) run its own dev server instead of reusing
// whatever already listens on 3000.
const port = Number(process.env.E2E_PORT ?? 3000);
const baseURL = `http://localhost:${port}`;

// proxy.ts fails closed without an operator session, so the dev server Playwright starts gets
// test-only login secrets and every test context starts signed in with a cookie minted from them.
// Always start the test server so its credentials match the browser session.
const E2E_OPERATOR_PASSWORD = "e2e-operator-password";
const E2E_SESSION_SECRET = "e2e-session-secret-0123456789abcdef";

export default defineConfig({
  testDir: "./tests/e2e",
  fullyParallel: false,
  retries: 1,
  workers: 1,
  use: {
    baseURL,
    trace: "retain-on-failure",
    storageState: {
      cookies: [
        {
          name: SESSION_COOKIE,
          value: createSessionToken(E2E_SESSION_SECRET),
          domain: "localhost",
          path: "/",
          expires: -1,
          httpOnly: true,
          secure: false,
          sameSite: "Strict",
        },
      ],
      origins: [],
    },
  },
  webServer: {
    command: `npm run dev -- -p ${port}`,
    url: baseURL,
    reuseExistingServer: false,
    timeout: 60_000,
    env: {
      OPERATOR_PASSWORD: E2E_OPERATOR_PASSWORD,
      SESSION_SECRET: E2E_SESSION_SECRET,
    },
  },
});

import { defineConfig } from "@playwright/test";
import { createSessionToken, SESSION_COOKIE } from "./src/lib/operatorAuth";

// The operator-login gate (src/proxy.ts) sends every page to /login when OPERATOR_PASSWORD and
// SESSION_SECRET are unset, so the dev server gets test-only values and the browser starts signed in.
const E2E_OPERATOR_PASSWORD = "e2e-only-operator-password";
const E2E_SESSION_SECRET = "e2e-only-session-secret-not-for-any-deploy";
const PORT = Number(process.env.E2E_PORT || 3000);

export default defineConfig({
  testDir: "./tests/e2e",
  fullyParallel: false,
  retries: 1,
  workers: 1,
  use: {
    baseURL: `http://localhost:${PORT}`,
    trace: "retain-on-failure",
    storageState: {
      cookies: [{
        name: SESSION_COOKIE, value: createSessionToken(E2E_SESSION_SECRET), domain: "localhost", path: "/",
        expires: -1, httpOnly: true, secure: false, sameSite: "Strict",
      }],
      origins: [],
    },
  },
  webServer: {
    command: `npx next dev -p ${PORT}`,
    url: `http://localhost:${PORT}`,
    // A reused server would not have the test credentials, so every page would redirect to /login.
    reuseExistingServer: false,
    timeout: 90_000,
    env: { ...process.env, OPERATOR_PASSWORD: E2E_OPERATOR_PASSWORD, SESSION_SECRET: E2E_SESSION_SECRET } as Record<string, string>,
  },
});

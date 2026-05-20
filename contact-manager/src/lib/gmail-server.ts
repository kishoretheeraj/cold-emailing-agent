// Server-only Gmail API client — never import this from a client component.
// Throws at runtime if any of the three OAuth env vars are missing.
// The "server-only" sentinel causes a build error if accidentally bundled client-side.
import "server-only";

import { google } from "googleapis";
import type { gmail_v1 } from "googleapis";

export function getGmailClient(): gmail_v1.Gmail {
  const clientId = process.env.GOOGLE_OAUTH_CLIENT_ID;
  const clientSecret = process.env.GOOGLE_OAUTH_CLIENT_SECRET;
  const refreshToken = process.env.GOOGLE_OAUTH_REFRESH_TOKEN;

  if (!clientId || !clientSecret || !refreshToken) {
    throw new Error(
      "Gmail OAuth env vars missing: GOOGLE_OAUTH_CLIENT_ID, " +
        "GOOGLE_OAUTH_CLIENT_SECRET, GOOGLE_OAUTH_REFRESH_TOKEN"
    );
  }

  const auth = new google.auth.OAuth2(clientId, clientSecret);
  auth.setCredentials({ refresh_token: refreshToken });
  return google.gmail({ version: "v1", auth });
}

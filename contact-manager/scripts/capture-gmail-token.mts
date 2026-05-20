/**
 * One-time script to capture a Gmail OAuth refresh token.
 * Run: npx tsx scripts/capture-gmail-token.mts
 *
 * Prerequisites:
 *   - GCP project with Gmail API enabled
 *   - OAuth 2.0 Desktop credentials downloaded
 *   - GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET in .env
 *
 * After running, add GOOGLE_OAUTH_REFRESH_TOKEN to .env and Vercel env vars.
 */

import { createInterface } from "node:readline";
import { config } from "dotenv";
import { google } from "googleapis";

config(); // load .env

const clientId = process.env.GOOGLE_OAUTH_CLIENT_ID;
const clientSecret = process.env.GOOGLE_OAUTH_CLIENT_SECRET;

if (!clientId || !clientSecret) {
  console.error(
    "Error: GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET must be set in .env"
  );
  process.exit(1);
}

const oauth2Client = new google.auth.OAuth2(
  clientId,
  clientSecret,
  "urn:ietf:wg:oauth:2.0:oob" // Desktop app redirect — code shown in browser
);

// Scopes needed:
//   gmail.send    — /api/send-draft calls drafts.send()
//   gmail.compose — /api/update-draft calls drafts.update()
//   gmail.modify  — label management
//   gmail.readonly — draft ID lookup in gmail.py after IMAP APPEND
const SCOPES = [
  "https://www.googleapis.com/auth/gmail.send",
  "https://www.googleapis.com/auth/gmail.compose",
  "https://www.googleapis.com/auth/gmail.modify",
  "https://www.googleapis.com/auth/gmail.readonly",
];

const authUrl = oauth2Client.generateAuthUrl({
  access_type: "offline",
  scope: SCOPES,
  prompt: "consent", // force consent screen so refresh_token is always returned
});

console.log("\n1. Open this URL in your browser:\n");
console.log(authUrl);
console.log("\n2. Grant access and copy the authorization code shown.\n");

const rl = createInterface({ input: process.stdin, output: process.stdout });

rl.question("3. Paste the authorization code here: ", async (code) => {
  rl.close();
  try {
    const { tokens } = await oauth2Client.getToken(code.trim());
    if (!tokens.refresh_token) {
      console.error(
        "\nError: No refresh_token in response. Did you include prompt=consent?\n" +
          "Try revoking access at https://myaccount.google.com/permissions and re-running."
      );
      process.exit(1);
    }
    console.log("\nSuccess! Add this to .env and Vercel env vars:\n");
    console.log(`GOOGLE_OAUTH_REFRESH_TOKEN=${tokens.refresh_token}`);
    console.log(
      "\nNote: This token has no expiry as long as it is used regularly.\n" +
        "If it stops working, re-run this script to generate a new one.\n"
    );
  } catch (err) {
    console.error("Error exchanging code for token:", err);
    process.exit(1);
  }
});

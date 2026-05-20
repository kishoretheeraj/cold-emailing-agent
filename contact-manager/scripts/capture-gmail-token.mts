/**
 * One-time script to capture a Gmail OAuth refresh token.
 * Run: npx tsx scripts/capture-gmail-token.mts
 *
 * Prerequisites:
 *   - GCP project with Gmail API enabled
 *   - OAuth 2.0 Desktop credentials in .env
 *   - http://localhost:8080 added as an authorized redirect URI in GCP Console
 *
 * After running, add GOOGLE_OAUTH_REFRESH_TOKEN to .env and Vercel env vars.
 */

import { createServer } from "node:http";
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

const REDIRECT_PORT = 8080;
const REDIRECT_URI = `http://localhost:${REDIRECT_PORT}`;

const oauth2Client = new google.auth.OAuth2(clientId, clientSecret, REDIRECT_URI);

const SCOPES = [
  "https://www.googleapis.com/auth/gmail.send",
  "https://www.googleapis.com/auth/gmail.compose",
  "https://www.googleapis.com/auth/gmail.modify",
  "https://www.googleapis.com/auth/gmail.readonly",
];

const authUrl = oauth2Client.generateAuthUrl({
  access_type: "offline",
  scope: SCOPES,
  prompt: "consent",
});

console.log("\n1. Open this URL in your browser:\n");
console.log(authUrl);
console.log("\n2. Authorize the app — you'll be redirected to localhost:8080.");
console.log("   (The page will show a 'Cannot GET /' error — that's expected.)\n");
console.log("Waiting for redirect on http://localhost:8080 ...\n");

const server = createServer(async (req, res) => {
  const url = new URL(req.url ?? "/", REDIRECT_URI);
  const code = url.searchParams.get("code");
  const error = url.searchParams.get("error");

  if (error) {
    res.writeHead(200, { "Content-Type": "text/plain" });
    res.end(`Error: ${error}. Close this tab and check the terminal.`);
    server.close();
    console.error(`\nOAuth error: ${error}`);
    process.exit(1);
  }

  if (!code) {
    res.writeHead(200, { "Content-Type": "text/plain" });
    res.end("No code received. Close this tab and try again.");
    return;
  }

  res.writeHead(200, { "Content-Type": "text/plain" });
  res.end("Authorization successful! You can close this tab and return to the terminal.");
  server.close();

  try {
    const { tokens } = await oauth2Client.getToken(code);
    if (!tokens.refresh_token) {
      console.error(
        "\nError: No refresh_token in response.\n" +
          "Try revoking access at https://myaccount.google.com/permissions and re-running."
      );
      process.exit(1);
    }
    console.log("Success! Add this to .env and Vercel env vars:\n");
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

server.listen(REDIRECT_PORT);

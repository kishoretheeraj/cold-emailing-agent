export const runtime = "nodejs";

import { authConfig, createSessionToken, passwordMatches, sessionCookie } from "@/lib/operatorAuth";

// Slows password guessing; serverless instances share no memory for a real rate limit, so the
// operator password must also be long (16+ characters, see authConfig).
const FAILED_LOGIN_DELAY_MS = 750;

export async function POST(req: Request) {
  const config = authConfig();
  if (!config) {
    return Response.json({ error: "Login is not configured (OPERATOR_PASSWORD, SESSION_SECRET)" }, { status: 503 });
  }
  const body = await req.json().catch(() => null);
  if (!passwordMatches(body?.password, config.password)) {
    await new Promise((resolve) => setTimeout(resolve, FAILED_LOGIN_DELAY_MS));
    return Response.json({ error: "Wrong password" }, { status: 401 });
  }
  return Response.json({ ok: true }, { headers: { "Set-Cookie": sessionCookie(createSessionToken(config.secret)) } });
}

export const runtime = "nodejs";

import { createClient } from "@supabase/supabase-js";
import { authConfig, createSessionToken, passwordMatches, sessionCookie } from "@/lib/operatorAuth";

// The operator PIN can be short (4+ characters), so every attempt first goes through the
// operator_login_attempt RPC (migration 20261017000000): at most 5 attempts per 15 minutes and 20 a
// day, serialized in the database because serverless instances share no memory. The delay below
// only slows a single client further.
const FAILED_LOGIN_DELAY_MS = 750;

function getClient() {
  return createClient(process.env.NEXT_PUBLIC_SUPABASE_URL!, process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!);
}

export async function POST(req: Request) {
  const config = authConfig();
  if (!config) {
    return Response.json({ error: "Login is not configured (OPERATOR_PASSWORD, SESSION_SECRET)" }, { status: 503 });
  }
  // Fails closed: without the attempt budget a short PIN could be guessed, so no budget, no login.
  let allowed: unknown = null;
  try {
    const { data, error } = await getClient().rpc("operator_login_attempt");
    if (!error) allowed = data;
  } catch {
    allowed = null;
  }
  if (allowed === null) {
    return Response.json({ error: "Login is temporarily unavailable. Try again shortly." }, { status: 503 });
  }
  if (allowed !== true) {
    return Response.json({ error: "Too many attempts. Try again in 15 minutes." }, { status: 429 });
  }
  const body = await req.json().catch(() => null);
  if (!passwordMatches(body?.password, config.password)) {
    await new Promise((resolve) => setTimeout(resolve, FAILED_LOGIN_DELAY_MS));
    return Response.json({ error: "Wrong password" }, { status: 401 });
  }
  return Response.json({ ok: true }, { headers: { "Set-Cookie": sessionCookie(createSessionToken(config.secret)) } });
}

export const runtime = "nodejs";

import { clearedSessionCookie } from "@/lib/operatorAuth";

export async function POST() {
  return Response.json({ ok: true }, { headers: { "Set-Cookie": clearedSessionCookie() } });
}

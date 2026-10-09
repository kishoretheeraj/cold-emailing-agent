import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";
import { authConfig, SESSION_COOKIE, verifySessionToken } from "@/lib/operatorAuth";

const PUBLIC_PATHS = new Set(["/login", "/api/login", "/api/logout"]);

// Gates every page and API route behind the operator session. Fails closed: when OPERATOR_PASSWORD
// and SESSION_SECRET are unset or too short, only the public paths are reachable, so a missing env
// var on a deploy can never expose the mutating routes (send-draft and the rest).
export function proxy(request: NextRequest) {
  const config = authConfig();

  const { pathname, search } = request.nextUrl;
  if (PUBLIC_PATHS.has(pathname)) return NextResponse.next();

  if (config && verifySessionToken(request.cookies.get(SESSION_COOKIE)?.value, config.secret)) {
    return NextResponse.next();
  }
  if (pathname.startsWith("/api/")) {
    return NextResponse.json({ error: "Sign in required" }, { status: 401 });
  }
  const login = new URL("/login", request.url);
  login.searchParams.set("next", `${pathname}${search}`);
  return NextResponse.redirect(login);
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};

import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";
import { authConfig, SESSION_COOKIE, verifySessionToken } from "@/lib/operatorAuth";

const PUBLIC_PATHS = new Set(["/login", "/api/login", "/api/logout"]);

// Gates every page and API route behind the operator session once OPERATOR_PASSWORD and
// SESSION_SECRET are set. Until then the app behaves as before; the submit route still checks
// the session itself and refuses without one, so approvals fail closed either way.
export function proxy(request: NextRequest) {
  const config = authConfig();
  if (!config) return NextResponse.next();

  const { pathname, search } = request.nextUrl;
  if (PUBLIC_PATHS.has(pathname)) return NextResponse.next();

  if (verifySessionToken(request.cookies.get(SESSION_COOKIE)?.value, config.secret)) {
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

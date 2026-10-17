// Thin proxy to GitHub Actions workflow_dispatch.
export const runtime = "nodejs";
export const maxDuration = 15;

import { isOperatorRequest } from "@/lib/operatorAuth";
import { workflowDispatchUrl } from "@/lib/github";

export async function POST(req: Request) {
  if (!isOperatorRequest(req)) {
    return Response.json({ error: "Sign in required" }, { status: 401 });
  }
  const token = process.env.GITHUB_DISPATCH_TOKEN;
  if (!token) {
    return Response.json({ error: "GITHUB_DISPATCH_TOKEN not configured" }, { status: 500 });
  }

  const res = await fetch(
    workflowDispatchUrl("daily_agent.yml"),
    {
      method: "POST",
      headers: {
        Authorization: `Bearer ${token}`,
        Accept: "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ ref: "main" }),
    }
  );

  // GitHub returns 204 No Content on success.
  if (res.status === 204) return Response.json({ ok: true });

  const body = await res.text();
  return Response.json({ error: body }, { status: res.status });
}

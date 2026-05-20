// Thin proxy to GitHub Actions workflow_dispatch.
// No unit tests: logic is trivial and GITHUB_DISPATCH_TOKEN is a secret unavailable in CI.
export const runtime = "nodejs";
export const maxDuration = 15;

const REPO = "kishoretheeraj/cold-emailing-agent";
const WORKFLOW = "daily_agent.yml";

export async function POST(_req: Request) {
  const token = process.env.GITHUB_DISPATCH_TOKEN;
  if (!token) {
    return Response.json({ error: "GITHUB_DISPATCH_TOKEN not configured" }, { status: 500 });
  }

  const res = await fetch(
    `https://api.github.com/repos/${REPO}/actions/workflows/${WORKFLOW}/dispatches`,
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

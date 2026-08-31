export const runtime = "nodejs";

export async function POST(req: Request, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;

  const res = await fetch(
    "https://api.github.com/repos/kishoretheeraj/cold-emailing-agent/actions/workflows/apply_agent_submit.yml/dispatches",
    {
      method: "POST",
      headers: {
        Authorization: `Bearer ${process.env.GITHUB_DISPATCH_TOKEN}`,
        Accept: "application/vnd.github+json",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ ref: "main", inputs: { application_id: id } }),
    }
  );

  if (!res.ok) {
    return Response.json({ error: "Failed to trigger submit workflow" }, { status: 502 });
  }
  return Response.json({ ok: true });
}

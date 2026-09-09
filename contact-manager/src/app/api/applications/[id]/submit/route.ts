export const runtime = "nodejs";

export async function POST(req: Request, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;

  // job_applications.id is an INTEGER. Reject anything else before it reaches the
  // armed workflow -- this id ends up in that job's environment, so a non-numeric
  // value has no legitimate use here.
  if (!/^\d+$/.test(id)) {
    return Response.json({ error: "Invalid application id" }, { status: 400 });
  }

  const token = process.env.GITHUB_DISPATCH_TOKEN;
  if (!token) {
    return Response.json(
      { error: "GITHUB_DISPATCH_TOKEN is not configured" },
      { status: 500 }
    );
  }

  const res = await fetch(
    "https://api.github.com/repos/kishoretheeraj/cold-emailing-agent/actions/workflows/apply_agent_submit.yml/dispatches",
    {
      method: "POST",
      headers: {
        Authorization: `Bearer ${token}`,
        Accept: "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ ref: "main", inputs: { application_id: id } }),
    }
  );

  if (!res.ok) {
    // Surface GitHub's own reason -- a bare 502 makes a real dispatch failure undebuggable.
    // Guarded: the error path must never itself throw.
    let detail = "";
    try {
      detail = await res.text();
    } catch {
      detail = "";
    }
    return Response.json(
      { error: "Failed to trigger submit workflow", detail },
      { status: 502 }
    );
  }
  return Response.json({ ok: true });
}

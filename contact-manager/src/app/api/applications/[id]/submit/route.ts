export const runtime = "nodejs";

import { createClient } from "@supabase/supabase-js";

function getClient() {
  return createClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!
  );
}

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

  // approve_application is a SECURITY DEFINER RPC -- it is the only way approved_at can ever
  // be set (the anon key's table-level UPDATE grant excludes that column -- see migration
  // 20260925000000). It also re-checks stage/apply_preview server-side, closing the gap where
  // this route previously dispatched the ARMED workflow for any numeric id with no check that
  // the row was actually submittable. IMPORTANT: supabase-js RPC calls do NOT throw on a
  // Postgres exception -- the error comes back on the `error` field of the resolved value, so
  // it must be checked explicitly here, not caught with try/catch.
  const supabase = getClient();
  const { error: rpcError } = await supabase.rpc("approve_application", { p_id: Number(id) });
  if (rpcError) {
    return Response.json({ error: rpcError.message }, { status: 409 });
  }

  // C3: approve_application already set approved_at above. If the dispatch below fails --
  // either GitHub returns a non-ok response, or the fetch call itself throws (network outage,
  // DNS failure, etc.) -- approved_at would otherwise stay set forever with no role able to
  // clear it (it's excluded from anon's UPDATE grant), and every retry would just 409 against
  // approve_application's own already-approved guard. reset_approval is the recovery path for
  // exactly this case; it's guarded server-side to only clear a still-ready_to_submit row, so
  // calling it here can never un-approve a row that actually went on to submit successfully.
  let res: Response;
  try {
    res = await fetch(
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
  } catch (err) {
    await supabase.rpc("reset_approval", { p_id: Number(id) });
    return Response.json(
      { error: "Failed to trigger submit workflow", detail: String(err) },
      { status: 502 }
    );
  }

  if (!res.ok) {
    // Surface GitHub's own reason -- a bare 502 makes a real dispatch failure undebuggable.
    // Guarded: the error path must never itself throw.
    let detail = "";
    try {
      detail = await res.text();
    } catch {
      detail = "";
    }
    await supabase.rpc("reset_approval", { p_id: Number(id) });
    return Response.json(
      { error: "Failed to trigger submit workflow", detail },
      { status: 502 }
    );
  }
  return Response.json({ ok: true });
}

export const runtime = "nodejs";

import { createClient } from "@supabase/supabase-js";
import { approvalSigningKey, signApproval } from "@/lib/approvalSignature";
import { isOperatorRequest } from "@/lib/operatorAuth";

function getClient() {
  return createClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!
  );
}

export async function POST(req: Request, { params }: { params: Promise<{ id: string }> }) {
  // Checked here as well as in the proxy: approving is the one action that can lead to a real
  // application, so it never relies on route matching alone.
  if (!isOperatorRequest(req)) {
    return Response.json({ error: "Sign in required" }, { status: 401 });
  }

  const { id } = await params;

  // job_applications.id is an INTEGER. Reject anything else before it reaches the
  // armed workflow -- this id ends up in that job's environment, so a non-numeric
  // value has no legitimate use here.
  if (!/^\d+$/.test(id)) {
    return Response.json({ error: "Invalid application id" }, { status: 400 });
  }

  // The hash of the preview revision the human was shown. approve_application binds approval
  // to it, so a preview edited between page render and this tap is refused (409) instead of
  // being approved unseen.
  const body = await req.json().catch(() => null);
  const revisionHash = body?.revision_hash;
  if (typeof revisionHash !== "string" || !/^[0-9a-f]{64}$/.test(revisionHash)) {
    return Response.json({ error: "revision_hash is required" }, { status: 400 });
  }

  const signingKey = approvalSigningKey();
  if (!signingKey) {
    return Response.json({ error: "APPROVAL_SIGNING_KEY is not configured" }, { status: 503 });
  }

  // APPLY_SUBMIT_HOST=beelink: apply-submit.service on the Beelink polls approved rows, so the
  // approval alone queues the submit and no GitHub workflow is dispatched.
  const beelinkSubmits = process.env.APPLY_SUBMIT_HOST === "beelink";
  const token = process.env.GITHUB_DISPATCH_TOKEN;
  if (!beelinkSubmits && !token) {
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
  // The signature is what the submit worker verifies (approval_signature.py); the RPC only
  // stores it and checks that the signing time is current.
  const supabase = getClient();
  const signedAtMs = Date.now();
  const { error: rpcError } = await supabase.rpc("approve_application", {
    p_id: Number(id),
    p_revision_hash: revisionHash,
    p_signature: signApproval(signingKey, Number(id), revisionHash, signedAtMs),
    p_signed_at_ms: signedAtMs,
  });
  if (rpcError) {
    return Response.json({ error: rpcError.message }, { status: 409 });
  }
  if (beelinkSubmits) {
    return Response.json({ ok: true, queued: "beelink" });
  }

  // C3: approve_application already set approved_at above. If the dispatch below fails --
  // either GitHub returns a non-ok response, or the fetch call itself throws (network outage,
  // DNS failure, etc.) -- approved_at would otherwise stay set forever with no role able to
  // clear it (it's excluded from anon's UPDATE grant), and every retry would just 409 against
  // approve_application's own already-approved guard. reset_approval is the recovery path for
  // exactly this case; it's guarded server-side to only clear a still-ready_to_submit row, so
  // calling it here can never un-approve a row that actually went on to submit successfully.
  // I5: reset_approval's own result must be checked (and the call itself guarded) same as
  // res.text() just below -- if reset_approval fails silently, the row is left stuck approved
  // with nobody the wiser; if the RPC call itself throws (plausible here, we're already in a
  // network-failure code path), an unguarded await would let that throw escape this catch
  // block and turn the intended 502-with-detail into a generic 500. Never let a reset_approval
  // failure prevent the original 502 response from reaching the client.
  async function tryResetApproval() {
    try {
      const { error } = await supabase.rpc("reset_approval", { p_id: Number(id) });
      if (error) {
        console.error(`reset_approval failed for application ${id}: ${error.message}`);
      }
    } catch (err) {
      console.error(`reset_approval threw for application ${id}: ${String(err)}`);
    }
  }

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
    await tryResetApproval();
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
    await tryResetApproval();
    return Response.json(
      { error: "Failed to trigger submit workflow", detail },
      { status: 502 }
    );
  }
  return Response.json({ ok: true });
}

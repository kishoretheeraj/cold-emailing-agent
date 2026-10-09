"use client";

import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import { openTakeover } from "@/lib/takeover";
import type { JobApplication } from "@/lib/types";

export const TAKEOVER_POLL_MS = 15_000;

// Shows every application a Beelink worker is holding open for a human (a CAPTCHA, a code, a
// login). The browser is reached over noVNC on the tailnet; "I'm done" lets the worker re-check
// the page and carry on. Nothing here clicks anything on the employer's site.
export function TakeoverBanner() {
  const [waiting, setWaiting] = useState<JobApplication[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const viewerUrl = process.env.NEXT_PUBLIC_TAKEOVER_URL;

  const load = useCallback(async () => {
    try {
      const res = await fetch("/api/applications?takeover=open");
      if (!res.ok) return;
      const body = (await res.json()) as { applications?: JobApplication[] };
      setWaiting((body.applications ?? []).filter((a) => openTakeover(a)));
    } catch {
      // Best-effort: the next poll tries again.
    }
  }, []);

  useEffect(() => {
    load();
    const timer = setInterval(load, TAKEOVER_POLL_MS);
    return () => clearInterval(timer);
  }, [load]);

  async function done(id: string) {
    setBusy(id);
    try {
      const res = await fetch(`/api/applications/${id}/takeover-continue`, { method: "POST" });
      if (res.ok) {
        toast.success("Thanks -- the worker is checking the page again");
      } else {
        const body = (await res.json().catch(() => ({}))) as { error?: string };
        toast.error(body.error ?? "Could not reach the worker");
      }
    } catch {
      toast.error("Could not reach the worker");
    }
    setBusy(null);
    load();
  }

  if (waiting.length === 0) return null;

  return (
    <section aria-label="Needs you" className="flex flex-col gap-2">
      {waiting.map((app) => {
        const request = openTakeover(app);
        return (
          <div
            key={app.id}
            role="alert"
            className="flex flex-wrap items-center gap-3 rounded-lg border border-amber-500/50 bg-amber-500/10 px-4 py-3 text-sm"
          >
            <div className="flex-1 min-w-[12rem]">
              <p className="font-medium text-amber-300">
                Needs you: {app.company} &middot; {app.role}
              </p>
              <p className="text-fg-muted">{request?.reason ?? "The worker is waiting for a person."}</p>
            </div>
            {viewerUrl ? (
              <a
                href={viewerUrl}
                target="_blank"
                rel="noreferrer"
                className="rounded-md border border-border-strong px-3 py-2 text-fg"
              >
                Open the browser
              </a>
            ) : (
              <span className="text-fg-dim">Open noVNC for display 1 on the Beelink</span>
            )}
            <button
              type="button"
              onClick={() => done(app.id)}
              disabled={busy === app.id}
              className="rounded-md bg-indigo-600 px-3 py-2 text-white disabled:opacity-50"
            >
              I&apos;m done
            </button>
          </div>
        );
      })}
    </section>
  );
}

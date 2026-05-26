"use client";

import { useCallback, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { toast } from "sonner";

type TriggerState = "idle" | "loading" | "ok" | "err";

const NAV_LINKS = [
  { href: "/overview", label: "Overview" },
  { href: "/queue", label: "Queue" },
  { href: "/replies", label: "Replies" },
  { href: "/prompts", label: "Prompts" },
  { href: "/runs", label: "Activity" },
] as const;

export function Nav() {
  const pathname = usePathname();
  const [triggerState, setTriggerState] = useState<TriggerState>("idle");

  const runAgent = useCallback(async () => {
    setTriggerState("loading");
    try {
      const r = await fetch("/api/trigger-agent", { method: "POST" });
      setTriggerState(r.ok ? "ok" : "err");
      if (r.ok) toast.success("Agent triggered — check Activity in ~30s");
      else toast.error("Failed to trigger agent");
    } catch {
      setTriggerState("err");
      toast.error("Failed to trigger agent");
    }
    setTimeout(() => setTriggerState("idle"), 3000);
  }, []);

  return (
    <nav className="h-16 border-b border-border flex items-center px-4 sm:px-6 gap-3">
      <Link
        href="/"
        className="text-sm font-semibold text-fg mr-2 shrink-0"
      >
        Cold Email Ops
      </Link>

      <div className="flex items-center gap-1.5 flex-1">
        {NAV_LINKS.map(({ href, label }) => {
          const active = pathname === href;
          return (
            <Link
              key={href}
              href={href}
              className={`rounded-lg border px-3 py-1.5 text-xs transition ${
                active
                  ? "border-indigo-500/40 bg-indigo-500/10 text-indigo-300"
                  : "border-border text-fg-muted hover:text-fg hover:border-border-strong"
              }`}
            >
              {label}
            </Link>
          );
        })}
      </div>

      <button
        type="button"
        onClick={runAgent}
        disabled={triggerState === "loading"}
        className={`shrink-0 rounded-lg border px-3 py-1.5 text-xs transition disabled:opacity-50 ${
          triggerState === "ok"
            ? "text-emerald-400 border-emerald-500/40"
            : triggerState === "err"
            ? "text-red-400 border-red-500/40"
            : "text-fg-muted border-border hover:text-fg hover:border-border-strong"
        }`}
      >
        {triggerState === "loading"
          ? "Triggering…"
          : triggerState === "ok"
          ? "Triggered ✓"
          : triggerState === "err"
          ? "Failed ✗"
          : "Run Agent"}
      </button>
    </nav>
  );
}

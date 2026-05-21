"use client";

import { useCallback, useState } from "react";
import Link from "next/link";
import { toast } from "sonner";
import { SmartInput } from "./SmartInput";
import { StructuredForm } from "./StructuredForm";
import { ContactsList } from "./ContactsList";

type InputMode = "smart" | "form";
type TriggerState = "idle" | "loading" | "ok" | "err";

export function App() {
  const [mode, setMode] = useState<InputMode>("smart");
  const [refreshKey, setRefreshKey] = useState(0);
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

  const onAdded = useCallback(() => {
    setRefreshKey((k) => k + 1);
    toast.success("Contact added — agent picks this up tomorrow at 4:37am");
  }, []);

  const onError = useCallback((msg: string) => {
    toast.error(msg);
  }, []);

  const onSuccess = useCallback((msg: string) => {
    toast.success(msg);
  }, []);

  return (
    <div className="mx-auto max-w-5xl px-4 sm:px-6 py-8 space-y-6">
      <header className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold text-fg">Cold Email Ops</h1>
          <p className="text-sm text-fg-muted mt-0.5">
            Add a contact below — the agent drafts a personalized email tomorrow at 4:37am.
          </p>
        </div>
        <nav className="shrink-0 flex items-center gap-2">
          <Link
            href="/overview"
            className="flex items-center gap-1 rounded-lg border border-border px-3 py-1.5 text-xs text-fg-muted hover:text-fg hover:border-border-strong transition"
          >
            Overview
          </Link>
          <Link
            href="/queue"
            className="flex items-center gap-1 rounded-lg border border-border px-3 py-1.5 text-xs text-fg-muted hover:text-fg hover:border-border-strong transition"
          >
            Queue
          </Link>
          <Link
            href="/replies"
            className="flex items-center gap-1 rounded-lg border border-border px-3 py-1.5 text-xs text-fg-muted hover:text-fg hover:border-border-strong transition"
          >
            Replies
          </Link>
          <Link
            href="/prompts"
            className="flex items-center gap-1 rounded-lg border border-border px-3 py-1.5 text-xs text-fg-muted hover:text-fg hover:border-border-strong transition"
          >
            Prompts
          </Link>
          <Link
            href="/runs"
            className="flex items-center gap-1 rounded-lg border border-border px-3 py-1.5 text-xs text-fg-muted hover:text-fg hover:border-border-strong transition"
          >
            Activity
          </Link>
          <button
            type="button"
            onClick={runAgent}
            disabled={triggerState === "loading"}
            className={`rounded-lg border px-3 py-1.5 text-xs transition disabled:opacity-50 ${
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
      </header>

      <section>
        <div className="bg-surface border border-border rounded-lg p-4 space-y-4">
          <div className="inline-flex rounded-lg border border-border bg-bg p-1">
            <ModeButton
              active={mode === "smart"}
              label="Smart Input"
              onClick={() => setMode("smart")}
            />
            <ModeButton
              active={mode === "form"}
              label="Structured Form"
              onClick={() => setMode("form")}
            />
          </div>

          <div
            key={mode}
            className="animate-[fadein_180ms_ease-out]"
            style={{
              animationName: "fadein",
            }}
          >
            {mode === "smart" ? (
              <SmartInput onAdded={onAdded} onError={onError} />
            ) : (
              <StructuredForm onAdded={onAdded} onError={onError} />
            )}
          </div>
        </div>
      </section>

      <section>
        <ContactsList
          refreshKey={refreshKey}
          onError={onError}
          onSuccess={onSuccess}
        />
      </section>

      <style>{`
        @keyframes fadein {
          from { opacity: 0; transform: translateY(4px); }
          to { opacity: 1; transform: translateY(0); }
        }
      `}</style>
    </div>
  );
}

function ModeButton({
  active,
  label,
  onClick,
}: {
  active: boolean;
  label: string;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      className={`rounded-md px-4 py-1.5 text-xs font-medium transition ${
        active ? "bg-indigo-500 text-white" : "text-fg-muted hover:text-fg"
      }`}
    >
      {label}
    </button>
  );
}

"use client";

import { useCallback, useState } from "react";
import { SmartInput } from "./SmartInput";
import { StructuredForm } from "./StructuredForm";
import { ContactsList } from "./ContactsList";
import { Toast, type ToastTone } from "./Toast";

type InputMode = "smart" | "form";

export function App() {
  const [mode, setMode] = useState<InputMode>("smart");
  const [refreshKey, setRefreshKey] = useState(0);
  const [toast, setToast] = useState<
    { msg: string; tone: ToastTone } | null
  >(null);

  const onAdded = useCallback(() => {
    setRefreshKey((k) => k + 1);
    setToast({
      msg: "Contact added — agent picks this up tomorrow 8am",
      tone: "success",
    });
  }, []);

  const onError = useCallback((msg: string) => {
    setToast({ msg, tone: "error" });
  }, []);

  const onUpdated = useCallback(() => {
    setToast({ msg: "Status updated", tone: "success" });
  }, []);

  return (
    <div className="mx-auto max-w-4xl px-4 sm:px-6 py-8 sm:py-12 space-y-10">
      <header className="space-y-1">
        <h1 className="text-2xl sm:text-3xl font-semibold tracking-tight text-fg">
          Cold Email Ops
        </h1>
        <p className="text-sm text-fg-muted">
          Add a contact below — the agent drafts a personalized email tomorrow at 8am.
        </p>
      </header>

      <section className="space-y-5">
        <div className="inline-flex rounded-lg border border-border bg-surface p-1">
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
      </section>

      <section className="space-y-3">
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-semibold text-fg">Recent contacts</h2>
          <span className="text-xs text-fg-dim">last 20</span>
        </div>
        <ContactsList
          refreshKey={refreshKey}
          onError={onError}
          onUpdated={onUpdated}
        />
      </section>

      {toast && (
        <Toast
          message={toast.msg}
          tone={toast.tone}
          onClose={() => setToast(null)}
        />
      )}

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

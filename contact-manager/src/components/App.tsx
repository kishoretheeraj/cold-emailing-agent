"use client";

import { useCallback, useState } from "react";
import { toast } from "sonner";
import { SmartInput } from "./SmartInput";
import { StructuredForm } from "./StructuredForm";
import { ContactsList } from "./ContactsList";

type InputMode = "smart" | "form";

export function App() {
  const [mode, setMode] = useState<InputMode>("smart");
  const [refreshKey, setRefreshKey] = useState(0);

  const onAdded = useCallback(() => {
    setRefreshKey((k) => k + 1);
    toast.success("Contact added — agent picks this up tomorrow 8am");
  }, []);

  const onError = useCallback((msg: string) => {
    toast.error(msg);
  }, []);

  const onSuccess = useCallback((msg: string) => {
    toast.success(msg);
  }, []);

  return (
    <div className="mx-auto max-w-5xl px-4 sm:px-6 py-8 space-y-6">
      <header className="flex items-baseline justify-between">
        <div>
          <h1 className="text-xl font-semibold text-fg">Cold Email Ops</h1>
          <p className="text-sm text-fg-muted mt-0.5">
            Add a contact below — the agent drafts a personalized email tomorrow at 8am.
          </p>
        </div>
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

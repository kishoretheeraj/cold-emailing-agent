"use client";

import { ReactNode } from "react";

export function Label({
  children,
  required,
}: {
  children: ReactNode;
  required?: boolean;
}) {
  return (
    <label className="block text-xs uppercase tracking-wider text-fg-muted mb-1.5">
      {children}
      {required && <span className="text-indigo-400 ml-0.5">*</span>}
    </label>
  );
}

const baseInput =
  "w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm text-fg placeholder-fg-dim transition focus:border-indigo-500 focus:outline-none";

export function TextInput(
  props: React.InputHTMLAttributes<HTMLInputElement>
) {
  return <input {...props} className={`${baseInput} ${props.className ?? ""}`} />;
}

export function TextArea(
  props: React.TextareaHTMLAttributes<HTMLTextAreaElement>
) {
  return (
    <textarea
      {...props}
      className={`${baseInput} resize-y min-h-[6rem] ${props.className ?? ""}`}
    />
  );
}

export function ToggleSwitch({
  on,
  onChange,
  label,
}: {
  on: boolean;
  onChange: (next: boolean) => void;
  label: string;
}) {
  return (
    <button
      type="button"
      onClick={() => onChange(!on)}
      className={`flex items-center gap-3 group select-none`}
    >
      <span
        className={`relative inline-flex h-5 w-9 items-center rounded-full transition ${
          on ? "bg-indigo-500" : "bg-[#34343b]"
        }`}
      >
        <span
          className={`inline-block h-3.5 w-3.5 transform rounded-full bg-white transition ${
            on ? "translate-x-5" : "translate-x-1"
          }`}
        />
      </span>
      <span className="text-sm text-fg-muted group-hover:text-fg">
        {label}
      </span>
    </button>
  );
}

export function TierSelector({
  value,
  onChange,
}: {
  value: number;
  onChange: (next: number) => void;
}) {
  const tiers = [
    { v: 1, label: "Tier 1 — Dream" },
    { v: 2, label: "Tier 2 — Strong" },
    { v: 3, label: "Tier 3 — Worth a shot" },
  ];
  return (
    <div className="grid grid-cols-3 gap-2">
      {tiers.map((t) => {
        const active = value === t.v;
        return (
          <button
            key={t.v}
            type="button"
            onClick={() => onChange(t.v)}
            className={`rounded-lg border px-3 py-2 text-xs transition ${
              active
                ? "border-indigo-500 bg-indigo-500/10 text-indigo-300"
                : "border-border bg-surface text-fg-muted hover:border-border-strong"
            }`}
          >
            {t.label}
          </button>
        );
      })}
    </div>
  );
}

"use client";

import type { ReactNode } from "react";

type BadgeVariant = "default" | "indigo" | "emerald" | "amber" | "red" | "muted";

const variantClasses: Record<BadgeVariant, string> = {
  default: "bg-surface-2 text-fg-muted border-border",
  indigo: "bg-indigo-500/15 text-indigo-300 border-indigo-500/30",
  emerald: "bg-emerald-500/15 text-emerald-300 border-emerald-500/30",
  amber: "bg-amber-500/15 text-amber-300 border-amber-500/30",
  red: "bg-red-500/15 text-red-300 border-red-500/30",
  muted: "bg-surface text-fg-dim border-border",
};

export function Badge({
  variant = "default",
  children,
  className = "",
}: {
  variant?: BadgeVariant;
  children: ReactNode;
  className?: string;
}) {
  return (
    <span
      className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-xs font-medium border whitespace-nowrap ${variantClasses[variant]} ${className}`}
    >
      {children}
    </span>
  );
}

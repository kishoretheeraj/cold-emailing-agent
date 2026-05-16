"use client";

import type { ReactNode } from "react";

export function EmptyState({
  icon,
  title,
  description,
  action,
}: {
  icon: ReactNode;
  title: string;
  description?: string;
  action?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-center justify-center py-12 text-center">
      <div className="w-12 h-12 rounded-full bg-surface-2 flex items-center justify-center mb-4">
        {icon}
      </div>
      <p className="text-fg text-base font-medium">{title}</p>
      {description && (
        <p className="text-fg-muted text-sm max-w-sm mt-1">{description}</p>
      )}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

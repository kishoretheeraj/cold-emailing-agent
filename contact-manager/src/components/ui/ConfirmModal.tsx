"use client";

import type { ReactNode } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { Loader2 } from "lucide-react";

export function ConfirmModal({
  open,
  title,
  body,
  confirmLabel,
  confirmVariant = "primary",
  onConfirm,
  onCancel,
  loading = false,
}: {
  open: boolean;
  title: string;
  body: ReactNode;
  confirmLabel: string;
  confirmVariant?: "primary" | "danger";
  onConfirm: () => void | Promise<void>;
  onCancel: () => void;
  loading?: boolean;
}) {
  if (!open) return null;

  const confirmClasses =
    confirmVariant === "danger"
      ? "bg-red-500/20 text-red-200 border border-red-500/40 hover:bg-red-500/30"
      : "bg-indigo-500/20 text-indigo-200 border border-indigo-500/40 hover:bg-indigo-500/30";

  return (
    <Dialog.Root
      open={open}
      onOpenChange={(o) => {
        if (!o) onCancel();
      }}
    >
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 bg-black/60 backdrop-blur-sm z-[60]" />
        <Dialog.Content className="fixed left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 z-[70] bg-surface border border-border max-w-md w-full rounded-xl p-6 outline-none">
          <Dialog.Title className="text-fg text-lg font-semibold mb-3">
            {title}
          </Dialog.Title>
          <Dialog.Description asChild>
            <div className="text-fg-muted text-sm space-y-3">{body}</div>
          </Dialog.Description>
          <div className="flex justify-end gap-2 mt-6">
            <button
              type="button"
              autoFocus
              disabled={loading}
              onClick={onCancel}
              className="px-4 py-2 text-sm text-fg-muted hover:text-fg transition-colors rounded-md border border-transparent hover:border-border disabled:opacity-50"
            >
              Cancel
            </button>
            <button
              type="button"
              disabled={loading}
              onClick={onConfirm}
              className={`px-4 py-2 text-sm rounded-md transition-colors flex items-center gap-2 disabled:opacity-50 ${confirmClasses}`}
            >
              {loading && <Loader2 className="size-3.5 animate-spin" />}
              {confirmLabel}
            </button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

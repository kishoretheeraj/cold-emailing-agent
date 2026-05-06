"use client";

import { useEffect } from "react";

export type ToastTone = "success" | "error";

export function Toast({
  message,
  tone = "success",
  onClose,
}: {
  message: string;
  tone?: ToastTone;
  onClose: () => void;
}) {
  useEffect(() => {
    const t = setTimeout(onClose, 4000);
    return () => clearTimeout(t);
  }, [onClose]);

  const accent =
    tone === "success"
      ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-200"
      : "border-red-500/40 bg-red-500/10 text-red-200";

  return (
    <div className="fixed inset-x-0 bottom-6 z-50 flex justify-center px-4 pointer-events-none">
      <div
        role="status"
        className={`pointer-events-auto rounded-xl border px-4 py-3 text-sm shadow-lg backdrop-blur ${accent}`}
      >
        {message}
      </div>
    </div>
  );
}

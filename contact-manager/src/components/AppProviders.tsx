"use client";

import { Toaster } from "sonner";
import { TooltipProvider } from "@/components/ui/Tooltip";

export default function AppProviders({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <TooltipProvider delayDuration={200}>
      {children}
      <Toaster
        theme="dark"
        position="bottom-right"
        richColors
        closeButton
        toastOptions={{
          style: {
            background: "var(--color-surface)",
            border: "1px solid var(--color-border)",
            color: "var(--color-fg)",
          },
        }}
      />
    </TooltipProvider>
  );
}

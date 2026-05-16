"use client";

import type { ReactNode } from "react";
import { Drawer } from "vaul";
import { X } from "lucide-react";

export function Sheet({
  open,
  onOpenChange,
  children,
}: {
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  children: ReactNode;
}) {
  return (
    <Drawer.Root direction="right" open={open} onOpenChange={onOpenChange}>
      {children}
    </Drawer.Root>
  );
}

export const SheetTrigger = Drawer.Trigger;

export function SheetContent({ children }: { children: ReactNode }) {
  return (
    <Drawer.Portal>
      <Drawer.Overlay className="fixed inset-0 bg-black/60 backdrop-blur-sm z-40" />
      <Drawer.Content className="fixed right-0 top-0 bottom-0 z-50 w-[440px] max-w-[90vw] bg-surface border-l border-border flex flex-col outline-none">
        {children}
      </Drawer.Content>
    </Drawer.Portal>
  );
}

export function SheetHeader({ children }: { children: ReactNode }) {
  return (
    <div className="relative px-6 py-4 border-b border-border">
      {children}
    </div>
  );
}

export function SheetTitle({ children }: { children: ReactNode }) {
  return (
    <Drawer.Title className="text-fg text-lg font-semibold">
      {children}
    </Drawer.Title>
  );
}

export function SheetDescription({ children }: { children: ReactNode }) {
  return (
    <Drawer.Description className="text-fg-muted text-sm mt-1">
      {children}
    </Drawer.Description>
  );
}

export function SheetBody({ children }: { children: ReactNode }) {
  return (
    <div className="flex-1 overflow-y-auto px-6 py-4">{children}</div>
  );
}

export function SheetFooter({ children }: { children: ReactNode }) {
  return (
    <div className="px-6 py-4 border-t border-border flex items-center justify-end gap-2">
      {children}
    </div>
  );
}

export function SheetClose({ onClick }: { onClick?: () => void }) {
  return (
    <Drawer.Close
      onClick={onClick}
      className="absolute top-3 right-3 size-7 rounded-md hover:bg-surface-2 inline-flex items-center justify-center text-fg-dim hover:text-fg transition-colors"
      aria-label="Close"
    >
      <X className="size-4" />
    </Drawer.Close>
  );
}

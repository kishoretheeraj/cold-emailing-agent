"use client";

import * as RadixSelect from "@radix-ui/react-select";
import { ChevronDown, Check } from "lucide-react";
import type { ReactNode } from "react";

export const Select = RadixSelect.Root;
export const SelectValue = RadixSelect.Value;
export const SelectGroup = RadixSelect.Group;
export const SelectLabel = RadixSelect.Label;

export function SelectTrigger({
  children,
  className = "",
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <RadixSelect.Trigger
      className={`flex items-center justify-between gap-2 px-3 py-2 bg-surface-2 border border-border rounded-md text-sm text-fg focus:outline-none focus:ring-2 focus:ring-indigo-500/40 data-[placeholder]:text-fg-dim ${className}`}
    >
      {children}
      <RadixSelect.Icon asChild>
        <ChevronDown className="size-4 text-fg-dim shrink-0" />
      </RadixSelect.Icon>
    </RadixSelect.Trigger>
  );
}

export function SelectContent({ children }: { children: ReactNode }) {
  return (
    <RadixSelect.Portal>
      <RadixSelect.Content
        position="popper"
        sideOffset={4}
        className="bg-surface border border-border rounded-md shadow-xl max-h-72 overflow-y-auto z-50 animate-in fade-in-0 zoom-in-95 w-[var(--radix-select-trigger-width)]"
      >
        <RadixSelect.Viewport className="p-1">{children}</RadixSelect.Viewport>
      </RadixSelect.Content>
    </RadixSelect.Portal>
  );
}

export function SelectItem({
  value,
  children,
}: {
  value: string;
  children: ReactNode;
}) {
  return (
    <RadixSelect.Item
      value={value}
      className="px-3 py-1.5 text-sm text-fg-muted cursor-pointer data-[highlighted]:bg-surface-2 data-[highlighted]:text-fg data-[state=checked]:text-indigo-300 flex items-center gap-2 outline-none rounded-sm"
    >
      <RadixSelect.ItemIndicator>
        <Check className="size-3.5" />
      </RadixSelect.ItemIndicator>
      <RadixSelect.ItemText>{children}</RadixSelect.ItemText>
    </RadixSelect.Item>
  );
}

export function SelectSeparator() {
  return <RadixSelect.Separator className="my-1 h-px bg-border" />;
}

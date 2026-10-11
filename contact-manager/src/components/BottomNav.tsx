"use client";

import Link from "next/link";
import { Activity, CheckCircle2, Inbox, Layers } from "lucide-react";
import type { LucideIcon } from "lucide-react";

type TabKey = "review" | "applications" | "done" | "system";

type BottomNavProps = {
  actionCount: number;
  progressCount: number;
  doneCount: number;
  active?: TabKey;
};

type Tab = {
  key: TabKey;
  label: string;
  href: string;
  Icon: LucideIcon;
  count: number;
};

export default function BottomNav({
  actionCount,
  progressCount,
  doneCount,
  active,
}: BottomNavProps) {
  const tabs: Tab[] = [
    { key: "review", label: "Review", href: "/", Icon: Inbox, count: actionCount },
    {
      key: "applications",
      label: "Applications",
      href: "/applications",
      Icon: Layers,
      count: progressCount,
    },
    {
      key: "done",
      label: "Done",
      href: "/applications?bucket=done",
      Icon: CheckCircle2,
      count: doneCount,
    },
    { key: "system", label: "System", href: "/overview", Icon: Activity, count: 0 },
  ];

  return (
    <nav
      aria-label="Primary"
      className="fixed inset-x-0 bottom-0 z-40 border-t border-border bg-surface pb-[env(safe-area-inset-bottom)] md:hidden"
    >
      <ul className="flex">
        {tabs.map(({ key, label, href, Icon, count }) => {
          const isActive = active === key;
          return (
            <li key={key} className="flex-1">
              <Link
                href={href}
                aria-current={isActive ? "page" : undefined}
                className={`flex min-h-[56px] flex-col items-center justify-center gap-0.5 text-xs ${
                  isActive ? "text-indigo-300" : "text-fg-muted"
                }`}
              >
                <span className="relative">
                  <Icon className="h-5 w-5" aria-hidden="true" />
                  {count > 0 && (
                    <span className="absolute -right-3 -top-2 min-w-[16px] rounded-full bg-indigo-600 px-1 text-center text-[10px] font-medium leading-4 text-white">
                      {count}
                    </span>
                  )}
                </span>
                <span>{label}</span>
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}

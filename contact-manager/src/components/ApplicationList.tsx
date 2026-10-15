"use client";

import type { KeyboardEvent } from "react";
import type { JobApplication } from "@/lib/types";
import ApplicationCard, { matchScore, relativeAge, statusLabel, statusTone } from "@/components/ApplicationCard";

export type ApplicationListProps = {
  apps: JobApplication[];
  onReview?: (app: JobApplication) => void;
  onViewDetail?: (app: JobApplication) => void;
  emptyMessage?: string;
};

export default function ApplicationList({
  apps,
  onReview,
  onViewDetail,
  emptyMessage = "No applications yet.",
}: ApplicationListProps) {
  if (apps.length === 0) {
    return <p className="py-8 text-center text-sm text-fg-muted">{emptyMessage}</p>;
  }

  const handleRowKey = (event: KeyboardEvent<HTMLTableRowElement>, app: JobApplication) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      onViewDetail?.(app);
    }
  };

  return (
    <>
      <ul aria-label="Applications" className="space-y-3 md:hidden">
        {apps.map((app) => (
          <li key={app.id} className="min-w-0">
            <ApplicationCard app={app} onReview={onReview} onViewDetail={onViewDetail} />
          </li>
        ))}
      </ul>

      <div className="hidden overflow-hidden rounded-xl border border-border md:block">
        <table aria-label="Applications" className="w-full table-fixed text-left text-sm">
          <thead className="border-b border-border text-xs text-fg-muted">
            <tr>
              <th scope="col" className="px-3 py-2 font-medium">Company</th>
              <th scope="col" className="px-3 py-2 font-medium">Role</th>
              <th scope="col" className="w-20 px-3 py-2 font-medium">Match</th>
              <th scope="col" className="w-40 px-3 py-2 font-medium">Status</th>
              <th scope="col" className="w-28 px-3 py-2 font-medium">Updated</th>
            </tr>
          </thead>
          <tbody>
            {apps.map((app) => {
              const score = matchScore(app);
              return (
                <tr
                  key={app.id}
                  tabIndex={0}
                  onClick={() => onViewDetail?.(app)}
                  onKeyDown={(event) => handleRowKey(event, app)}
                  className="cursor-pointer border-b border-border last:border-b-0 hover:bg-surface"
                >
                  <td className="truncate px-3 py-2 text-fg">{app.company}</td>
                  <td className="truncate px-3 py-2 text-fg-muted">{app.role}</td>
                  <td className="px-3 py-2 text-indigo-300">{score != null ? score : "--"}</td>
                  <td className="px-3 py-2">
                    <span
                      className={`inline-block max-w-full truncate rounded-md border px-2 py-0.5 text-xs font-medium ${statusTone(app.automation_status)}`}
                    >
                      {statusLabel(app.automation_status)}
                    </span>
                  </td>
                  <td className="px-3 py-2 text-fg-dim">{relativeAge(app.updated_at) ?? "--"}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </>
  );
}

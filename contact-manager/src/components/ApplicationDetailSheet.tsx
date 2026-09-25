"use client";

import { useEffect, useState } from "react";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetBody,
  SheetClose,
} from "@/components/ui/Sheet";
import type { JobApplication } from "@/lib/types";

type Files = {
  resume_url: string | null;
  resume_error: boolean;
  cover_letter_url: string | null;
  cover_letter_error: boolean;
};

const EMPTY_FILES: Files = {
  resume_url: null,
  resume_error: false,
  cover_letter_url: null,
  cover_letter_error: false,
};

const SNAPSHOT_TEXT_FIELDS = [
  ["description", "Description"],
  ["responsibilities", "Responsibilities"],
  ["qualifications", "Qualifications"],
  ["benefits", "Benefits"],
  ["location", "Location"],
] as const;

// M13: job_discovery.py/jobright.py can store a snapshot field as an array (e.g.
// responsibilities as a list of bullet strings), not just a plain string. A string-only reader
// would silently drop these fields for postings sourced that way -- render an array as a list
// of separate items (one per entry) instead of hiding it or flattening it into one paragraph.
function snapshotValue(
  snapshot: Record<string, unknown> | null,
  key: string
): string | string[] | null {
  if (!snapshot) return null;
  const v = snapshot[key];
  if (typeof v === "string" && v.trim()) return v;
  if (Array.isArray(v)) {
    const lines = v.filter((item): item is string => typeof item === "string" && item.trim() !== "");
    return lines.length > 0 ? lines : null;
  }
  return null;
}

export function ApplicationDetailSheet({
  application,
  onClose,
}: {
  application: JobApplication | null;
  onClose: () => void;
}) {
  const [files, setFiles] = useState<Files>(EMPTY_FILES);
  const [filesLoading, setFilesLoading] = useState(false);

  useEffect(() => {
    if (!application) {
      setFiles(EMPTY_FILES);
      return;
    }
    let cancelled = false;
    setFilesLoading(true);
    fetch(`/api/applications/${application.id}/files`)
      .then((res) => res.json())
      .then((data) => {
        if (!cancelled) {
          setFiles({
            resume_url: data.resume_url ?? null,
            resume_error: data.resume_error ?? false,
            cover_letter_url: data.cover_letter_url ?? null,
            cover_letter_error: data.cover_letter_error ?? false,
          });
        }
      })
      .catch(() => {
        // A network-level failure to even reach /files is itself an error state, not a
        // "no file" state -- I11's distinction applies here too.
        if (!cancelled) setFiles({ ...EMPTY_FILES, resume_error: true, cover_letter_error: true });
      })
      .finally(() => {
        if (!cancelled) setFilesLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // M11: intentionally keyed on application?.id only, not the whole `application` object --
    // I7's onSaved gives the parent a new application object reference on every answer save,
    // and this effect must NOT re-fetch signed file URLs just because apply_preview text
    // changed. react-hooks/exhaustive-deps would otherwise flag this; suppress with a reason
    // rather than widen the dependency array and reintroduce the wasteful refetch.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [application?.id]);

  return (
    <Sheet open={application !== null} onOpenChange={(o) => !o && onClose()}>
      <SheetContent>
        {application && (
          <>
            <SheetHeader>
              <SheetTitle>
                {application.company} -- {application.role}
              </SheetTitle>
              <SheetClose onClick={onClose} />
            </SheetHeader>
            <SheetBody>
              <div className="flex flex-col gap-6">
                <section>
                  <h3 className="text-sm font-medium text-fg mb-2">Job</h3>
                  {application.job_url ? (
                    <a
                      href={application.job_url}
                      target="_blank"
                      rel="noreferrer"
                      className="text-indigo-400 text-sm underline break-all"
                    >
                      {application.job_url}
                    </a>
                  ) : (
                    <p className="text-fg-dim text-sm">No job URL on file.</p>
                  )}
                  <div className="flex flex-col gap-3 mt-3">
                    {SNAPSHOT_TEXT_FIELDS.map(([key, label]) => {
                      const value = snapshotValue(application.posting_snapshot, key);
                      if (!value) return null;
                      return (
                        <div key={key}>
                          <p className="text-xs text-fg-dim uppercase tracking-wide">{label}</p>
                          {Array.isArray(value) ? (
                            <ul className="list-disc pl-4 text-sm text-fg-muted">
                              {value.map((line, i) => (
                                <li key={i}>{line}</li>
                              ))}
                            </ul>
                          ) : (
                            <p className="text-sm text-fg-muted whitespace-pre-wrap">{value}</p>
                          )}
                        </div>
                      );
                    })}
                  </div>
                </section>

                <section>
                  <h3 className="text-sm font-medium text-fg mb-2">Resume</h3>
                  {filesLoading ? (
                    <p className="text-fg-dim text-sm">Loading...</p>
                  ) : files.resume_error ? (
                    <p className="text-red-400 text-sm">Couldn't load your resume -- try again.</p>
                  ) : files.resume_url ? (
                    <iframe
                      title="Resume"
                      src={files.resume_url}
                      className="w-full h-64 border border-border rounded-md"
                    />
                  ) : (
                    <p className="text-fg-dim text-sm">No resume on file yet.</p>
                  )}
                </section>

                <section>
                  <h3 className="text-sm font-medium text-fg mb-2">Cover letter</h3>
                  {filesLoading ? (
                    <p className="text-fg-dim text-sm">Loading...</p>
                  ) : files.cover_letter_error ? (
                    <p className="text-red-400 text-sm">Couldn't load your cover letter -- try again.</p>
                  ) : files.cover_letter_url ? (
                    <iframe
                      title="Cover letter"
                      src={files.cover_letter_url}
                      className="w-full h-64 border border-border rounded-md"
                    />
                  ) : (
                    <p className="text-fg-dim text-sm">No cover letter on file yet.</p>
                  )}
                </section>
              </div>
            </SheetBody>
          </>
        )}
      </SheetContent>
    </Sheet>
  );
}

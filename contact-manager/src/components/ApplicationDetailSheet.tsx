"use client";

import { useEffect, useState } from "react";
import { toast } from "sonner";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetBody,
  SheetClose,
} from "@/components/ui/Sheet";
import type { JobApplication } from "@/lib/types";
import { Badge } from "@/components/ui/Badge";
import { pickVerdictVariant } from "@/lib/applicationBadges";

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

function AnswerEditor({
  title,
  answers,
  multiline,
  onChange,
}: {
  title: string;
  answers: Record<string, string>;
  multiline: boolean;
  onChange: (key: string, value: string) => void;
}) {
  const entries = Object.entries(answers);
  if (entries.length === 0) {
    return (
      <div>
        <p className="text-xs text-fg-dim uppercase tracking-wide">{title}</p>
        <p className="text-sm text-fg-dim">None.</p>
      </div>
    );
  }
  return (
    <div className="flex flex-col gap-2">
      <p className="text-xs text-fg-dim uppercase tracking-wide">{title}</p>
      {entries.map(([question, value]) =>
        multiline ? (
          <label key={question} className="flex flex-col gap-1 text-sm">
            <span className="text-fg-muted">{question}</span>
            <textarea
              value={value}
              onChange={(e) => onChange(question, e.target.value)}
              className="px-2 py-1 bg-surface-2 border border-border rounded-md text-sm text-fg min-h-[60px]"
            />
          </label>
        ) : (
          <label key={question} className="flex flex-col gap-1 text-sm">
            <span className="text-fg-muted">{question}</span>
            <input
              value={value}
              onChange={(e) => onChange(question, e.target.value)}
              className="px-2 py-1 bg-surface-2 border border-border rounded-md text-sm text-fg"
            />
          </label>
        )
      )}
    </div>
  );
}

export function ApplicationDetailSheet({
  application,
  onClose,
  onSaved,
}: {
  application: JobApplication | null;
  onClose: () => void;
  onSaved?: (app: JobApplication) => void;
}) {
  const [files, setFiles] = useState<Files>(EMPTY_FILES);
  const [filesLoading, setFilesLoading] = useState(false);
  const [screeningAnswers, setScreeningAnswers] = useState<Record<string, string>>({});
  const [eligibilityAnswers, setEligibilityAnswers] = useState<Record<string, string>>({});
  const [savingAnswers, setSavingAnswers] = useState(false);

  useEffect(() => {
    setScreeningAnswers(application?.apply_preview?.screening_answers ?? {});
    setEligibilityAnswers(application?.apply_preview?.eligibility_answers ?? {});
  }, [application?.id, application?.apply_preview]);

  const handleSaveAnswers = async () => {
    if (!application || !application.apply_preview) return;
    setSavingAnswers(true);
    try {
      const res = await fetch(`/api/applications/${application.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          apply_preview: {
            ...application.apply_preview,
            screening_answers: screeningAnswers,
            eligibility_answers: eligibilityAnswers,
          },
        }),
      });
      if (!res.ok) throw new Error("request failed");
      const data = await res.json();
      toast.success("Answers saved");
      // I7: without this, a successful save updates the database but the table's answer
      // count and a re-opened/re-rendered sheet both keep showing the pre-edit data --
      // defeating U11's entire point (the human's edit should be what actually gets
      // submitted, and they should be able to SEE that it was saved).
      if (data.application) onSaved?.(data.application as JobApplication);
    } catch {
      toast.error("Could not save answers");
    } finally {
      setSavingAnswers(false);
    }
  };

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

                <section>
                  <h3 className="text-sm font-medium text-fg mb-2">Application preview</h3>
                  {application.apply_preview ? (
                    <div className="flex flex-col gap-4">
                      <p className="text-sm text-fg-muted">Platform: {application.apply_preview.platform}</p>
                      <div>
                        <p className="text-xs text-fg-dim uppercase tracking-wide">Field values</p>
                        <div className="flex flex-col gap-1 mt-1">
                          {Object.entries(application.apply_preview.field_values).map(([k, v]) => (
                            <p key={k} className="text-sm text-fg-muted">
                              <span className="text-fg-dim">{k}:</span> {v}
                            </p>
                          ))}
                        </div>
                      </div>
                      <AnswerEditor
                        title="Eligibility answers"
                        answers={eligibilityAnswers}
                        multiline={false}
                        onChange={(k, v) => setEligibilityAnswers((cur) => ({ ...cur, [k]: v }))}
                      />
                      <AnswerEditor
                        title="Screening answers"
                        answers={screeningAnswers}
                        multiline={true}
                        onChange={(k, v) => setScreeningAnswers((cur) => ({ ...cur, [k]: v }))}
                      />
                      <button
                        type="button"
                        onClick={handleSaveAnswers}
                        disabled={savingAnswers}
                        className="px-3 py-2 bg-indigo-600 text-white rounded-md text-sm w-fit disabled:opacity-50"
                      >
                        {savingAnswers ? "Saving..." : "Save changes"}
                      </button>
                    </div>
                  ) : (
                    <p className="text-fg-dim text-sm">No application preview yet.</p>
                  )}
                </section>

                <section>
                  <h3 className="text-sm font-medium text-fg mb-2">Pick</h3>
                  {application.pick_verdict ? (
                    <div className="flex flex-col gap-2">
                      <Badge variant={pickVerdictVariant(application.pick_verdict)}>
                        {application.pick_verdict}
                      </Badge>
                      {application.pick_score !== null && (
                        <p className="text-sm text-fg-muted">Score: {application.pick_score}</p>
                      )}
                      {application.pick_reasoning && (
                        <p className="text-sm text-fg-muted whitespace-pre-wrap">
                          {application.pick_reasoning}
                        </p>
                      )}
                    </div>
                  ) : (
                    <p className="text-fg-dim text-sm">Not yet scored.</p>
                  )}
                </section>

                {(application.resume_cost_usd !== null ||
                  application.resume_tokens_input !== null ||
                  application.resume_tokens_output !== null) && (
                  <section>
                    <h3 className="text-sm font-medium text-fg mb-2">Cost</h3>
                    <div className="flex flex-col gap-1 text-sm text-fg-muted">
                      {application.resume_cost_usd !== null && (
                        <p>Cost: ${application.resume_cost_usd.toFixed(4)}</p>
                      )}
                      {application.resume_tokens_input !== null && (
                        <p>Input tokens: {application.resume_tokens_input}</p>
                      )}
                      {application.resume_tokens_output !== null && (
                        <p>Output tokens: {application.resume_tokens_output}</p>
                      )}
                    </div>
                  </section>
                )}
              </div>
            </SheetBody>
          </>
        )}
      </SheetContent>
    </Sheet>
  );
}

"use client";

import { useState, useEffect, useRef } from "react";
import { supabase, resolveInsertError } from "@/lib/supabase";
import type { ReviewContact, BulkImportWindow } from "@/lib/types";
import { Label, TextInput, TierSelector } from "./Field";

// ── Local types ────────────────────────────────────────────────────────────────

type Phase = "reviewing" | "summary" | "importing" | "done" | "error";

type ImportResult = {
  index: number;
  name: string;
  ok: boolean;
  error?: string;
};

// ── Component ──────────────────────────────────────────────────────────────────

export function ReviewFlow({
  contacts,
  onUpdate,
  onBack,
  onAddMore,
  onAdded,
  onError,
}: {
  contacts: ReviewContact[];
  onUpdate: (index: number, updated: ReviewContact) => void;
  onBack: () => void;
  onAddMore: () => void;
  onAdded: (window?: BulkImportWindow) => void;
  onError: (message: string) => void;
}) {
  const [phase, setPhase] = useState<Phase>("reviewing");
  const [currentIndex, setCurrentIndex] = useState(0);
  const [slideDir, setSlideDir] = useState<"forward" | "backward">("forward");
  const [importResults, setImportResults] = useState<ImportResult[]>([]);
  const [hasRetried, setHasRetried] = useState(false);
  const [importStartedAt, setImportStartedAt] = useState<number | null>(null);
  const [importEndedAt, setImportEndedAt] = useState<number | null>(null);
  const [inlineError, setInlineError] = useState<string | null>(null);

  const contact = contacts[currentIndex] ?? contacts[0];
  const confirmedCount = contacts.filter((c) => c.status === "confirmed").length;

  useEffect(() => {
    setInlineError(null);
  }, [currentIndex]);

  // ── Navigation helpers ───────────────────────────────────────────────────

  function navigate(toIdx: number, dir: "forward" | "backward") {
    setSlideDir(dir);
    setCurrentIndex(toIdx);
  }

  function goBack() {
    if (confirmedCount > 0) {
      if (!window.confirm("Go back to input? Confirmed contacts will be lost.")) return;
    }
    onBack();
  }

  function skip(idx: number) {
    const updated = { ...contacts[idx], status: "skipped" as const };
    onUpdate(idx, updated);
    const rest = contacts.map((c, i) => (i === idx ? updated : c));
    const hasPending = rest.some((c) => c.status === "pending");
    if (!hasPending) {
      setPhase("summary");
    } else if (idx < rest.length - 1) {
      navigate(idx + 1, "forward");
    } else {
      const first = rest.findIndex((c) => c.status === "pending");
      if (first >= 0) navigate(first, "forward");
      else setPhase("summary");
    }
  }

  // ── Validation + confirm ─────────────────────────────────────────────────

  const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

  function tryConfirm(idx: number): boolean {
    const c = contacts[idx];

    if (c.missing_email) {
      if (!c.email || !c.email.trim()) {
        setInlineError("Add an email address to confirm");
        return false;
      }
      if (!EMAIL_RE.test(c.email.trim())) {
        setInlineError("Enter a valid email address");
        return false;
      }
    }

    if (c.missing_required && (c.required_missing_fields ?? []).length > 0) {
      const stillMissing = (c.required_missing_fields ?? []).filter((f) => {
        if (f === "name") return !c.name?.trim();
        if (f === "company") return !c.company?.trim();
        return false;
      });
      if (stillMissing.length > 0) {
        setInlineError("Fill the required fields above to confirm");
        return false;
      }
    }

    const confirmed = { ...c, status: "confirmed" as const };
    onUpdate(idx, confirmed);
    setInlineError(null);

    const rest = contacts.map((rc, i) => (i === idx ? confirmed : rc));
    const hasPending = rest.some((rc) => rc.status === "pending");
    if (!hasPending) {
      setPhase("summary");
    } else if (idx < rest.length - 1) {
      navigate(idx + 1, "forward");
    } else {
      const first = rest.findIndex((rc) => rc.status === "pending");
      if (first >= 0) navigate(first, "forward");
      else setPhase("summary");
    }
    return true;
  }

  // ── Keyboard + touch ─────────────────────────────────────────────────────

  const handlersRef = useRef({
    confirm: () => { tryConfirm(currentIndex); },
    prev: () => { if (currentIndex > 0) navigate(currentIndex - 1, "backward"); },
    skip: () => { skip(currentIndex); },
    back: goBack,
  });

  handlersRef.current = {
    confirm: () => { tryConfirm(currentIndex); },
    prev: () => { if (currentIndex > 0) navigate(currentIndex - 1, "backward"); },
    skip: () => { skip(currentIndex); },
    back: goBack,
  };

  useEffect(() => {
    if (phase !== "reviewing") return;
    function onKey(e: KeyboardEvent) {
      const el = document.activeElement as HTMLElement | null;
      const tag = el?.tagName?.toLowerCase() ?? "";
      if (
        tag === "input" ||
        tag === "textarea" ||
        tag === "select" ||
        el?.getAttribute("contenteditable") !== null
      )
        return;
      if (e.key === "Enter" || e.key === "ArrowRight") {
        e.preventDefault();
        handlersRef.current.confirm();
      } else if (e.key === "ArrowLeft") {
        e.preventDefault();
        handlersRef.current.prev();
      } else if (e.key === "s" || e.key === "S") {
        handlersRef.current.skip();
      } else if (e.key === "Escape") {
        handlersRef.current.back();
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [phase]); // eslint-disable-line react-hooks/exhaustive-deps

  const touchStartX = useRef(0);

  // ── Import sequence ──────────────────────────────────────────────────────

  async function runImport(toImport: ReviewContact[]) {
    const startedAt = Date.now();
    setImportStartedAt(startedAt);
    setPhase("importing");

    const results: ImportResult[] = toImport.map((c, i) => ({
      index: i,
      name: c.name ?? "",
      ok: false,
    }));
    setImportResults(results);

    try {
      for (let i = 0; i < toImport.length; i++) {
        const c = toImport[i];
        try {
          const { error } = await supabase.from("contacts").insert({
            name: c.name,
            email: c.email,
            company: c.company,
            role: c.role,
            detail: c.detail,
            tier: c.tier ?? 2,
            mode: c.mode ?? "outreach",
            dartmouth: c.dartmouth ?? false,
            notes: c.notes,
            resume_url: c.resume_url ?? null,
            stage: "new",
            reply_status: "no_reply",
          });
          if (error) throw new Error(await resolveInsertError(error, c.email ?? ""));
          setImportResults((rs) =>
            rs.map((r, j) => (j === i ? { ...r, ok: true } : r))
          );
        } catch (err) {
          setImportResults((rs) =>
            rs.map((r, j) =>
              j === i
                ? { ...r, ok: false, error: err instanceof Error ? err.message : "failed" }
                : r
            )
          );
        }
      }
      const endedAt = Date.now();
      setImportEndedAt(endedAt);
      setPhase("done");
    } catch {
      setImportEndedAt(Date.now());
      setPhase("error");
    }
  }

  async function retryContacts(indices: number[], toImport: ReviewContact[]) {
    setHasRetried(true);
    for (const i of indices) {
      const c = toImport[i];
      try {
        const { error } = await supabase.from("contacts").insert({
          name: c.name,
          email: c.email,
          company: c.company,
          role: c.role,
          detail: c.detail,
          tier: c.tier ?? 2,
          mode: c.mode ?? "outreach",
          dartmouth: c.dartmouth ?? false,
          notes: c.notes,
          resume_url: c.resume_url ?? null,
          stage: "new",
          reply_status: "no_reply",
        });
        if (error) throw new Error(await resolveInsertError(error, c.email ?? ""));
        setImportResults((rs) =>
          rs.map((r, j) => (j === i ? { ...r, ok: true, error: undefined } : r))
        );
      } catch (err) {
        setImportResults((rs) =>
          rs.map((r, j) =>
            j === i
              ? { ...r, error: err instanceof Error ? err.message : "failed" }
              : r
          )
        );
      }
    }
    setPhase("done");
  }

  // ── Summary helpers ──────────────────────────────────────────────────────

  const confirmed = contacts.filter((c) => c.status === "confirmed");
  const skipped = contacts.filter(
    (c) => c.status === "skipped" && !c.missing_email && !c.missing_required
  );
  const noEmail = contacts.filter(
    (c) => c.missing_email && c.status !== "confirmed"
  );
  const missingReq = contacts.filter(
    (c) => c.missing_required && !c.missing_email && c.status !== "confirmed"
  );
  const notReviewed = contacts.filter(
    (c) => c.status === "pending" && !c.missing_email && !c.missing_required
  );
  const dartmouthInConfirmed = confirmed.filter((c) => c.dartmouth);

  // ── Phase: summary ───────────────────────────────────────────────────────

  if (phase === "summary") {
    return (
      <div className="space-y-4 max-w-lg mx-auto">
        <h3 className="text-xl font-semibold text-fg text-center">Ready to import</h3>

        {confirmed.length > 15 && (
          <div className="rounded-lg border border-yellow-600/40 bg-yellow-900/20 px-4 py-3 text-sm text-yellow-300">
            Importing many contacts at once may affect email deliverability.
            Consider spreading across multiple days using the Tier system.
          </div>
        )}

        <div className="rounded-xl border border-border bg-surface-2 overflow-hidden divide-y divide-border">
          <SummarySection
            label={`Will be imported (${confirmed.length})`}
            names={confirmed.map((c) => c.name ?? "")}
            nameClass="text-fg"
          />
          {skipped.length > 0 && (
            <SummarySection
              label={`Skipped (${skipped.length})`}
              names={skipped.map((c) => c.name ?? "")}
              nameClass="text-fg-muted"
            />
          )}
          {noEmail.length > 0 && (
            <SummarySection
              label={`Missing email -- cannot import (${noEmail.length})`}
              names={noEmail.map((c) => c.name ?? "")}
              nameClass="text-fg-dim"
            />
          )}
          {missingReq.length > 0 && (
            <SummarySection
              label={`Missing required field -- cannot import (${missingReq.length})`}
              names={missingReq.map(
                (c) => `${c.name ?? ""} (missing: ${(c.required_missing_fields ?? []).join(", ")})`
              )}
              nameClass="text-fg-dim"
            />
          )}
          {notReviewed.length > 0 && (
            <div className="p-4 space-y-1">
              <div className="text-sm font-medium text-fg-muted">
                Not reviewed ({notReviewed.length})
              </div>
              <ul className="pl-4 space-y-0.5">
                {notReviewed.map((c, i) => (
                  <li key={i} className="text-sm text-fg-dim">
                    {c.name ?? ""}
                  </li>
                ))}
              </ul>
              <p className="text-xs text-fg-dim pl-4">These will not be imported.</p>
            </div>
          )}
        </div>

        {dartmouthInConfirmed.length > 0 && (
          <div className="rounded-lg border-l-2 border-indigo-500 border border-border bg-surface-2 px-4 py-3 text-sm text-indigo-300">
            {dartmouthInConfirmed.length} Tuck{" "}
            {dartmouthInConfirmed.length === 1 ? "alumnus" : "alumni"} detected.
            Alumni tone will be applied when the agent sends their emails.
          </div>
        )}

        <div className="flex gap-3 pt-1">
          <button
            onClick={() => {
              setCurrentIndex(0);
              setSlideDir("forward");
              setPhase("reviewing");
            }}
            className="flex-1 rounded-lg border border-border px-4 py-2.5 text-sm text-fg-muted hover:text-fg transition"
          >
            Review again
          </button>
          <button
            onClick={() => runImport(confirmed)}
            disabled={confirmed.length === 0}
            className="flex-1 rounded-lg bg-indigo-500 px-4 py-2.5 text-sm font-medium text-white transition hover:bg-indigo-400 disabled:bg-indigo-500/30 disabled:cursor-not-allowed"
          >
            Import {confirmed.length} contact{confirmed.length !== 1 ? "s" : ""}
          </button>
        </div>
      </div>
    );
  }

  // ── Phase: importing ─────────────────────────────────────────────────────

  if (phase === "importing") {
    return (
      <div className="space-y-4 max-w-lg mx-auto">
        <h3 className="text-xl font-semibold text-fg text-center">Importing contacts...</h3>
        <ImportResultsList results={importResults} />
      </div>
    );
  }

  // ── Phase: error ─────────────────────────────────────────────────────────

  if (phase === "error") {
    const confirmedContacts = contacts.filter((c) => c.status === "confirmed");
    const retryIndices = importResults
      .map((r, i) => (!r.ok ? i : -1))
      .filter((i) => i >= 0);

    return (
      <div className="space-y-4 max-w-lg mx-auto">
        <h3 className="text-xl font-semibold text-fg text-center">Import interrupted</h3>
        <p className="text-sm text-fg-muted text-center">
          Some contacts may have been inserted; some were not. Review below.
        </p>
        <ImportResultsList results={importResults} />
        <div className="flex gap-3">
          <button
            onClick={() => retryContacts(retryIndices, confirmedContacts)}
            disabled={hasRetried}
            className="flex-1 rounded-lg border border-border px-4 py-2.5 text-sm text-fg-muted hover:text-fg transition disabled:opacity-30 disabled:cursor-not-allowed"
          >
            Retry remaining
          </button>
          <button
            onClick={() => setPhase("done")}
            className="flex-1 rounded-lg bg-indigo-500 px-4 py-2.5 text-sm font-medium text-white transition hover:bg-indigo-400"
          >
            Done
          </button>
        </div>
      </div>
    );
  }

  // ── Phase: done ──────────────────────────────────────────────────────────

  if (phase === "done") {
    const okCount = importResults.filter((r) => r.ok).length;
    const failedCount = importResults.filter((r) => !r.ok).length;
    const confirmedContacts = contacts.filter((c) => c.status === "confirmed");
    const failedIndices = importResults
      .map((r, i) => (!r.ok ? i : -1))
      .filter((i) => i >= 0);

    return (
      <div className="space-y-5 max-w-md mx-auto text-center py-4">
        <div className="flex items-center justify-center">
          <div className="flex h-14 w-14 items-center justify-center rounded-full bg-emerald-500/15 border border-emerald-500/30">
            <svg
              className="h-7 w-7 text-emerald-400"
              fill="none"
              viewBox="0 0 24 24"
              stroke="currentColor"
              strokeWidth={2.5}
              aria-hidden="true"
            >
              <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
            </svg>
          </div>
        </div>

        <div>
          <h3 className="text-xl font-semibold text-fg">
            {okCount} contact{okCount !== 1 ? "s" : ""} added to your pipeline
          </h3>
          <p className="mt-1 text-sm text-fg-muted">
            The agent will pick them up on the next scheduled run.
          </p>
        </div>

        {failedCount > 0 && (
          <div className="space-y-2">
            <p className="text-sm text-red-300">
              {failedCount} contact{failedCount !== 1 ? "s" : ""} failed to import.
            </p>
            <button
              onClick={() => retryContacts(failedIndices, confirmedContacts)}
              disabled={hasRetried}
              className="rounded-lg border border-red-600/40 px-4 py-2 text-sm text-red-300 hover:bg-red-900/20 transition disabled:opacity-30 disabled:cursor-not-allowed"
            >
              Retry failed
            </button>
          </div>
        )}

        <div className="flex gap-3">
          <button
            onClick={() =>
              onAdded(
                importStartedAt !== null && importEndedAt !== null
                  ? { startedAt: importStartedAt, endedAt: importEndedAt }
                  : undefined
              )
            }
            className="flex-1 rounded-lg border border-border px-4 py-2.5 text-sm text-fg-muted hover:text-fg transition"
          >
            View contacts
          </button>
          <button
            onClick={onAddMore}
            className="flex-1 rounded-lg bg-indigo-500 px-4 py-2.5 text-sm font-medium text-white transition hover:bg-indigo-400"
          >
            Add more
          </button>
        </div>
      </div>
    );
  }

  // ── Phase: reviewing ─────────────────────────────────────────────────────

  const pct = contacts.length > 0 ? (confirmedCount / contacts.length) * 100 : 0;
  const isLastCard = currentIndex === contacts.length - 1;

  return (
    <div className="space-y-3">
      {/* Inject dynamic progress bar width without inline style prop */}
      <style>{`.review-prog-fill { width: ${pct.toFixed(1)}%; }`}</style>

      {/* ── Top bar ── */}
      <div className="space-y-1.5">
        <div className="flex items-center gap-3">
          <button
            onClick={goBack}
            className="shrink-0 rounded-lg border border-border px-3 py-1.5 text-xs text-fg-muted hover:text-fg transition"
          >
            Back to input
          </button>
          <div className="flex-1 space-y-1 min-w-0">
            <div className="h-1 rounded-full bg-surface-2 overflow-hidden">
              <div className="review-prog-fill h-full bg-indigo-500 transition-all duration-500 rounded-full" />
            </div>
            <div className="text-center text-xs text-fg-muted">
              Reviewing {currentIndex + 1} of {contacts.length}
            </div>
          </div>
          <div className="shrink-0 flex gap-1.5">
            {confirmedCount >= 1 && (
              <button
                onClick={() => setPhase("summary")}
                className="rounded-lg border border-border px-3 py-1.5 text-xs text-fg-muted hover:text-fg transition"
              >
                Jump to summary
              </button>
            )}
            <button
              onClick={() => skip(currentIndex)}
              className="rounded-lg border border-border px-3 py-1.5 text-xs text-fg-muted hover:text-fg transition"
            >
              Skip
            </button>
          </div>
        </div>
        <p className="text-center text-xs text-fg-dim">
          Enter to confirm  |  left/right to navigate  |  Esc to go back
        </p>
      </div>

      {/* ── Animated card ── */}
      <div
        key={currentIndex}
        className={`mx-auto max-w-2xl ${
          slideDir === "forward" ? "slide-from-right" : "slide-from-left"
        }`}
        onTouchStart={(e) => {
          touchStartX.current = e.touches[0].clientX;
        }}
        onTouchEnd={(e) => {
          const delta = e.changedTouches[0].clientX - touchStartX.current;
          if (delta < -50) tryConfirm(currentIndex);
          else if (delta > 50 && currentIndex > 0) navigate(currentIndex - 1, "backward");
        }}
      >
        <div className="rounded-xl border border-border bg-surface overflow-hidden flex divide-x divide-border">
          {/* ── Left column — identity ── */}
          <div className="w-2/5 shrink-0 p-4 space-y-3">
            {/* Badges */}
            <div className="space-y-1.5">
              {contact.dartmouth && (
                <div>
                  <span className="inline-flex items-center rounded-full bg-emerald-500/15 border border-emerald-500/20 px-2 py-0.5 text-xs text-emerald-300">
                    Tuck Alumni
                  </span>
                  {contact.notes?.startsWith("Tuck Class of") && (
                    <div className="text-xs text-fg-muted mt-0.5 pl-0.5">
                      {contact.notes}
                    </div>
                  )}
                </div>
              )}

              {contact.missing_required &&
                (contact.required_missing_fields ?? []).length > 0 && (
                  <span className="inline-flex items-center rounded-full bg-red-500/15 border border-red-500/20 px-2 py-0.5 text-xs text-red-300">
                    Missing: {(contact.required_missing_fields ?? []).join(", ")}
                  </span>
                )}

              {contact.missing_email && (
                <span className="inline-flex items-center rounded-full bg-red-500/15 border border-red-500/20 px-2 py-0.5 text-xs text-red-300">
                  No email
                </span>
              )}
            </div>

            {/* Always-editable identity fields */}
            <div>
              <Label>Name</Label>
              <TextInput
                value={contact.name ?? ""}
                placeholder="First and last name"
                className={(contact.required_missing_fields ?? []).includes("name") ? "border-red-500" : ""}
                onChange={(e) => {
                  const val = e.target.value;
                  const prev = contacts[currentIndex];
                  const newFields = (prev.required_missing_fields ?? []).filter(
                    (f) => f !== "name" || !val.trim()
                  );
                  onUpdate(currentIndex, {
                    ...prev,
                    name: val,
                    required_missing_fields: newFields,
                    missing_required: newFields.length > 0,
                  });
                }}
              />
            </div>

            <div>
              <Label>Email</Label>
              <TextInput
                type="email"
                value={contact.email ?? ""}
                placeholder="email@example.com"
                className={contact.missing_email ? "border-red-500" : ""}
                onChange={(e) => {
                  const val = e.target.value;
                  onUpdate(currentIndex, {
                    ...contacts[currentIndex],
                    email: val,
                    missing_email: !val.includes("@"),
                  });
                }}
              />
            </div>

            <div>
              <Label>Company</Label>
              <TextInput
                value={contact.company ?? ""}
                placeholder="Company name"
                className={(contact.required_missing_fields ?? []).includes("company") ? "border-red-500" : ""}
                onChange={(e) => {
                  const val = e.target.value;
                  const prev = contacts[currentIndex];
                  const newFields = (prev.required_missing_fields ?? []).filter(
                    (f) => f !== "company" || !val.trim()
                  );
                  onUpdate(currentIndex, {
                    ...prev,
                    company: val,
                    required_missing_fields: newFields,
                    missing_required: newFields.length > 0,
                  });
                }}
              />
            </div>

            <div>
              <Label>Role</Label>
              <TextInput
                value={contact.role ?? ""}
                placeholder="Job title"
                onChange={(e) => {
                  onUpdate(currentIndex, {
                    ...contacts[currentIndex],
                    role: e.target.value,
                  });
                }}
              />
            </div>
          </div>

          {/* ── Right column — editable fields ── */}
          <div className="flex-1 p-4 space-y-3">
            <div>
              <Label>Tier</Label>
              <TierSelector
                value={contact.tier ?? 2}
                onChange={(v) =>
                  onUpdate(currentIndex, { ...contacts[currentIndex], tier: v })
                }
              />
            </div>

            <div>
              <Label>Mode</Label>
              <div className="grid grid-cols-2 gap-1.5">
                {(["outreach", "applied"] as const).map((m) => {
                  const active = (contact.mode ?? "outreach") === m;
                  return (
                    <button
                      key={m}
                      type="button"
                      onClick={() =>
                        onUpdate(currentIndex, { ...contacts[currentIndex], mode: m })
                      }
                      className={`rounded-lg border py-2 text-xs capitalize transition ${
                        active
                          ? "border-indigo-500 bg-indigo-500/10 text-indigo-300"
                          : "border-border bg-surface text-fg-muted hover:border-border-strong"
                      }`}
                    >
                      {m}
                    </button>
                  );
                })}
              </div>
              {contact.mode === "applied" && (
                <p className="mt-1.5 text-xs text-fg-muted">
                  Applied mode requires a job description, which is not captured in bulk
                  paste. The agent will fall back to outreach behavior until you edit this
                  contact and add a JD.
                </p>
              )}
            </div>

            <div>
              <Label>Personalization hook</Label>
              <TextInput
                value={contact.detail ?? ""}
                placeholder="One specific thing to reference in the email"
                onChange={(e) =>
                  onUpdate(currentIndex, { ...contacts[currentIndex], detail: e.target.value })
                }
              />
            </div>

            <div>
              <Label>Resume link</Label>
              <TextInput
                value={contact.resume_url ?? ""}
                placeholder="Google Drive URL (optional)"
                onChange={(e) =>
                  onUpdate(currentIndex, {
                    ...contacts[currentIndex],
                    resume_url: e.target.value,
                  })
                }
              />
            </div>

            <div>
              <Label>Notes</Label>
              <TextInput
                value={contact.notes ?? ""}
                placeholder="Any extra context"
                onChange={(e) =>
                  onUpdate(currentIndex, { ...contacts[currentIndex], notes: e.target.value })
                }
              />
            </div>
          </div>
        </div>
      </div>

      {/* ── Bottom bar ── */}
      <div className="sticky bottom-0 rounded-xl border border-border bg-bg py-3 px-4">
        <div className="flex items-center justify-between gap-3">
          <button
            onClick={() => {
              if (currentIndex > 0) navigate(currentIndex - 1, "backward");
            }}
            disabled={currentIndex === 0}
            className="rounded-lg border border-border px-4 py-2 text-sm text-fg-muted transition hover:text-fg disabled:opacity-30 disabled:cursor-not-allowed"
          >
            Previous
          </button>

          <div className="flex-1 flex flex-col items-center gap-1 min-w-0">
            {contacts.length <= 20 ? (
              <div className="flex items-center gap-1.5 flex-wrap justify-center">
                {contacts.map((c, i) => {
                  const isCurrent = i === currentIndex;
                  let color = "border border-border-strong";
                  if (c.missing_email && c.status !== "confirmed") color = "bg-red-500";
                  else if (c.status === "confirmed") color = "bg-indigo-500";
                  else if (c.status === "skipped") color = "bg-fg-dim";
                  return (
                    <button
                      key={i}
                      type="button"
                      onClick={() =>
                        navigate(i, i > currentIndex ? "forward" : "backward")
                      }
                      className={`rounded-full transition-all ${color} ${
                        isCurrent ? "h-3 w-3 scale-125" : "h-2 w-2"
                      }`}
                      aria-label={`Go to contact ${i + 1}`}
                    />
                  );
                })}
              </div>
            ) : (
              <span className="text-xs text-fg-muted">
                {currentIndex + 1} of {contacts.length}
              </span>
            )}
            {inlineError && (
              <p className="text-xs text-red-400">{inlineError}</p>
            )}
          </div>

          <button
            onClick={() => tryConfirm(currentIndex)}
            className="rounded-lg bg-indigo-500 px-4 py-2 text-sm font-medium text-white transition hover:bg-indigo-400"
          >
            {isLastCard ? "Confirm and review" : "Confirm and next"}
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Shared sub-components ──────────────────────────────────────────────────────

function SummarySection({
  label,
  names,
  nameClass,
}: {
  label: string;
  names: string[];
  nameClass: string;
}) {
  return (
    <div className="p-4 space-y-2">
      <div className="text-sm font-medium text-fg">{label}</div>
      {names.length === 0 ? (
        <div className="text-xs text-fg-dim pl-4">None</div>
      ) : (
        <ul className="pl-4 space-y-0.5">
          {names.map((n, i) => (
            <li key={i} className={`text-sm ${nameClass}`}>
              {n}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function ImportResultsList({ results }: { results: ImportResult[] }) {
  return (
    <div className="rounded-xl border border-border bg-surface-2 overflow-hidden divide-y divide-border">
      {results.map((r, i) => (
        <div key={i} className="flex items-center gap-3 px-4 py-3">
          <span className="w-5 shrink-0 text-center">
            {r.ok ? (
              <svg
                className="h-4 w-4 text-emerald-400"
                fill="none"
                viewBox="0 0 24 24"
                stroke="currentColor"
                strokeWidth={2.5}
                aria-hidden="true"
              >
                <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
              </svg>
            ) : r.error ? (
              <svg
                className="h-4 w-4 text-red-400"
                fill="none"
                viewBox="0 0 24 24"
                stroke="currentColor"
                strokeWidth={2.5}
                aria-hidden="true"
              >
                <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
              </svg>
            ) : (
              <Spinner />
            )}
          </span>
          <span
            className={`flex-1 text-sm ${r.error ? "text-red-300" : "text-fg"}`}
          >
            {r.name}
          </span>
          {!r.ok && !r.error && (
            <span className="text-xs text-fg-dim">Waiting...</span>
          )}
          {r.ok && <span className="text-xs text-emerald-400">Added</span>}
          {r.error && (
            <span className="text-xs text-red-400 truncate max-w-[10rem]">
              {r.error}
            </span>
          )}
        </div>
      ))}
    </div>
  );
}

function Spinner() {
  return (
    <svg
      className="h-4 w-4 animate-spin text-fg-muted"
      viewBox="0 0 24 24"
      fill="none"
      aria-hidden="true"
    >
      <circle cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="3" opacity="0.25" />
      <path
        d="M22 12a10 10 0 0 0-10-10"
        stroke="currentColor"
        strokeWidth="3"
        strokeLinecap="round"
      />
    </svg>
  );
}

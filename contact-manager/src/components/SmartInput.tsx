"use client";

import { useState, useEffect, useRef } from "react";
import { supabase } from "@/lib/supabase";
import type { ExtractedContact, ReviewContact } from "@/lib/types";
import { Label, TextInput, TextArea, ToggleSwitch, TierSelector } from "./Field";

// ── Bulk-flow types ────────────────────────────────────────────────────────────

type ImportResult = {
  contact: ReviewContact;
  status: "pending" | "ok" | "error";
  error?: string;
};

type FlowMode = "input" | "review" | "summary" | "importing" | "done";

// ── Main component ─────────────────────────────────────────────────────────────

export function SmartInput({
  onAdded,
  onError,
}: {
  onAdded: () => void;
  onError: (msg: string) => void;
}) {
  // Single-contact state (unchanged UX)
  const [text, setText] = useState("");
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [preview, setPreview] = useState<ExtractedContact | null>(null);

  // Bulk-flow state
  const [flowMode, setFlowMode] = useState<FlowMode>("input");
  const [reviewContacts, setReviewContacts] = useState<ReviewContact[]>([]);
  const [currentIdx, setCurrentIdx] = useState(0);
  const [slideDir, setSlideDir] = useState<"forward" | "backward">("forward");
  const [importResults, setImportResults] = useState<ImportResult[]>([]);

  async function handleExtract() {
    if (!text.trim()) {
      onError("Paste some text first");
      return;
    }
    setLoading(true);
    try {
      const res = await fetch("/api/extract", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text }),
      });
      const json = await res.json();
      if (!res.ok) throw new Error(json.error || "extraction failed");

      if (json.is_bulk) {
        const contacts: ReviewContact[] = (
          json.contacts as (ExtractedContact & { missing_email?: boolean })[]
        ).map((c) => ({
          ...c,
          status: "pending" as const,
          missing_email: c.missing_email ?? false,
        }));
        setReviewContacts(contacts);
        setCurrentIdx(0);
        setSlideDir("forward");
        setFlowMode("review");
      } else {
        setPreview(json.contacts[0]);
      }
    } catch (err) {
      onError(err instanceof Error ? err.message : "extraction failed");
    } finally {
      setLoading(false);
    }
  }

  async function handleSingleConfirm() {
    if (!preview) return;
    if (!preview.name || !preview.email || !preview.company) {
      onError("Name, Email, and Company are required");
      return;
    }
    setSaving(true);
    try {
      const row = {
        name: preview.name,
        email: preview.email,
        company: preview.company,
        role: preview.role,
        detail: preview.detail,
        tier: preview.tier ?? 2,
        mode: preview.mode ?? "outreach",
        dartmouth: preview.dartmouth ?? false,
        job_title: preview.job_title,
        job_description: preview.job_description,
        applied_date: preview.applied_date,
        notes: preview.notes,
        stage: "new",
        reply_status: "no_reply",
      };
      const { error } = await supabase.from("contacts").insert(row);
      if (error) throw new Error(error.message);
      setText("");
      setPreview(null);
      onAdded();
    } catch (err) {
      onError(err instanceof Error ? err.message : "save failed");
    } finally {
      setSaving(false);
    }
  }

  function updateSingle<K extends keyof ExtractedContact>(key: K, value: ExtractedContact[K]) {
    setPreview((p) => (p ? { ...p, [key]: value } : p));
  }

  // ── Bulk helpers ───────────────────────────────────────────────────────────

  function updateCard(idx: number, updates: Partial<ReviewContact>) {
    setReviewContacts((cs) => cs.map((c, i) => (i === idx ? { ...c, ...updates } : c)));
  }

  function navigate(toIdx: number, dir: "forward" | "backward") {
    setSlideDir(dir);
    setCurrentIdx(toIdx);
  }

  /** Returns false if email is required but missing. */
  function tryConfirmCard(idx: number): boolean {
    const c = reviewContacts[idx];
    if (c.missing_email && (!c.email || !c.email.includes("@"))) return false;
    const updated = reviewContacts.map((rc, i) =>
      i === idx ? { ...rc, status: "confirmed" as const } : rc
    );
    setReviewContacts(updated);
    const hasPending = updated.some((rc) => rc.status === "pending");
    if (!hasPending) {
      setFlowMode("summary");
    } else if (idx < updated.length - 1) {
      navigate(idx + 1, "forward");
    } else {
      const first = updated.findIndex((rc) => rc.status === "pending");
      if (first >= 0) navigate(first, "forward");
      else setFlowMode("summary");
    }
    return true;
  }

  function skipCard(idx: number) {
    const updated = reviewContacts.map((rc, i) =>
      i === idx ? { ...rc, status: "skipped" as const } : rc
    );
    setReviewContacts(updated);
    const hasPending = updated.some((rc) => rc.status === "pending");
    if (!hasPending) {
      setFlowMode("summary");
    } else if (idx < updated.length - 1) {
      navigate(idx + 1, "forward");
    } else {
      const first = updated.findIndex((rc) => rc.status === "pending");
      if (first >= 0) navigate(first, "forward");
      else setFlowMode("summary");
    }
  }

  async function startImport(toImport: ReviewContact[]) {
    const results: ImportResult[] = toImport.map((c) => ({
      contact: c,
      status: "pending" as const,
    }));
    setImportResults(results);
    setFlowMode("importing");

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
          job_title: c.job_title,
          job_description: c.job_description,
          applied_date: c.applied_date,
          notes: c.notes,
          stage: "new",
          reply_status: "no_reply",
        });
        if (error) throw new Error(error.message);
        setImportResults((rs) =>
          rs.map((r, j) => (j === i ? { ...r, status: "ok" as const } : r))
        );
      } catch (err) {
        setImportResults((rs) =>
          rs.map((r, j) =>
            j === i
              ? { ...r, status: "error" as const, error: err instanceof Error ? err.message : "failed" }
              : r
          )
        );
      }
    }

    await new Promise((r) => setTimeout(r, 800));
    setFlowMode("done");
    onAdded();
  }

  async function retryFailed() {
    const failedIndices = importResults
      .map((r, i) => (r.status === "error" ? i : -1))
      .filter((i) => i >= 0);

    for (const i of failedIndices) {
      const c = importResults[i].contact;
      try {
        const { error } = await supabase.from("contacts").insert({
          name: c.name, email: c.email, company: c.company,
          role: c.role, detail: c.detail, tier: c.tier ?? 2,
          mode: c.mode ?? "outreach", dartmouth: c.dartmouth ?? false,
          job_title: c.job_title, job_description: c.job_description,
          applied_date: c.applied_date, notes: c.notes,
          stage: "new", reply_status: "no_reply",
        });
        if (error) throw new Error(error.message);
        setImportResults((rs) =>
          rs.map((r, j) => (j === i ? { ...r, status: "ok" as const, error: undefined } : r))
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
    onAdded();
  }

  function resetAll() {
    setText("");
    setPreview(null);
    setFlowMode("input");
    setReviewContacts([]);
    setCurrentIdx(0);
    setImportResults([]);
  }

  // ── Render bulk-flow screens ───────────────────────────────────────────────

  if (flowMode === "review") {
    return (
      <ReviewView
        contacts={reviewContacts}
        currentIdx={currentIdx}
        slideDir={slideDir}
        onBack={() => {
          setFlowMode("input");
          setReviewContacts([]);
        }}
        onNavigate={navigate}
        onUpdateCard={updateCard}
        onConfirm={tryConfirmCard}
        onSkip={skipCard}
      />
    );
  }

  if (flowMode === "summary") {
    return (
      <SummaryScreen
        contacts={reviewContacts}
        onReviewAgain={() => {
          setCurrentIdx(0);
          setSlideDir("forward");
          setFlowMode("review");
        }}
        onImport={startImport}
      />
    );
  }

  if (flowMode === "importing") {
    return <ImportProgressView results={importResults} />;
  }

  if (flowMode === "done") {
    return (
      <DoneScreen
        results={importResults}
        onViewPipeline={resetAll}
        onAddMore={resetAll}
        onRetryFailed={retryFailed}
      />
    );
  }

  // ── Input mode — single-contact flow (UX unchanged) ───────────────────────

  return (
    <div className="space-y-4">
      <div>
        <Label>Paste anything — LinkedIn bio, JD, or a casual description</Label>
        <TextArea
          value={text}
          onChange={(e) => setText(e.target.value)}
          rows={8}
          placeholder={
            "Examples:\n\n• Applied to Stripe for Senior PM, hiring manager is Sarah Kim sarah@stripe.com, she led Treasury launch\n\n• Dana Ehrlich, CEO of Clearbond, dana@clearbond.com, customs bond SaaS, Tuck MBA"
          }
        />
      </div>

      <div className="flex items-center gap-3">
        <button
          onClick={handleExtract}
          disabled={loading || !text.trim()}
          className="inline-flex items-center gap-2 rounded-lg bg-indigo-500 px-4 py-2 text-sm font-medium text-white transition hover:bg-indigo-400 disabled:cursor-not-allowed disabled:bg-indigo-500/30 disabled:text-indigo-200"
        >
          {loading && <Spinner />}
          {loading ? "Extracting…" : "Extract with Claude"}
        </button>
        {preview && (
          <button
            onClick={() => setPreview(null)}
            className="text-sm text-fg-muted hover:text-fg"
          >
            Discard preview
          </button>
        )}
      </div>

      {preview && (
        <div className="rounded-xl border border-border bg-surface-2 p-5 space-y-4 transition">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-semibold text-fg">
              Preview — edit any field before confirming
            </h3>
            <span className="rounded-full bg-indigo-500/10 px-2 py-0.5 text-xs text-indigo-300 border border-indigo-500/30">
              {preview.mode ?? "outreach"}
            </span>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <Field label="Name" required>
              <TextInput
                value={preview.name ?? ""}
                onChange={(e) => updateSingle("name", e.target.value)}
              />
            </Field>
            <Field label="Email" required>
              <TextInput
                type="email"
                value={preview.email ?? ""}
                onChange={(e) => updateSingle("email", e.target.value)}
              />
            </Field>
            <Field label="Company" required>
              <TextInput
                value={preview.company ?? ""}
                onChange={(e) => updateSingle("company", e.target.value)}
              />
            </Field>
            <Field label="Role / Title">
              <TextInput
                value={preview.role ?? ""}
                onChange={(e) => updateSingle("role", e.target.value)}
              />
            </Field>
          </div>

          <Field label="Detail to reference">
            <TextArea
              value={preview.detail ?? ""}
              onChange={(e) => updateSingle("detail", e.target.value)}
              rows={2}
            />
          </Field>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <div>
              <Label>Tier</Label>
              <TierSelector
                value={preview.tier ?? 2}
                onChange={(v) => updateSingle("tier", v)}
              />
            </div>
            <div>
              <Label>Mode</Label>
              <div className="grid grid-cols-2 gap-2">
                {(["outreach", "applied"] as const).map((m) => {
                  const active = (preview.mode ?? "outreach") === m;
                  return (
                    <button
                      key={m}
                      type="button"
                      onClick={() => updateSingle("mode", m)}
                      className={`rounded-lg border px-3 py-2 text-xs capitalize transition ${
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
            </div>
          </div>

          {preview.mode === "applied" && (
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              <Field label="Job Title Applied For">
                <TextInput
                  value={preview.job_title ?? ""}
                  onChange={(e) => updateSingle("job_title", e.target.value)}
                />
              </Field>
              <Field label="Applied Date">
                <TextInput
                  type="date"
                  value={preview.applied_date ?? ""}
                  onChange={(e) => updateSingle("applied_date", e.target.value)}
                />
              </Field>
              <div className="sm:col-span-2">
                <Field label="Job Description">
                  <TextArea
                    value={preview.job_description ?? ""}
                    onChange={(e) => updateSingle("job_description", e.target.value)}
                    rows={5}
                  />
                </Field>
              </div>
            </div>
          )}

          <ToggleSwitch
            on={preview.dartmouth ?? false}
            onChange={(v) => updateSingle("dartmouth", v)}
            label="Dartmouth / Tuck / Thayer / Irving connection"
          />

          <Field label="Notes">
            <TextArea
              value={preview.notes ?? ""}
              onChange={(e) => updateSingle("notes", e.target.value)}
              rows={2}
            />
          </Field>

          <div className="flex items-center justify-end gap-2 pt-2">
            <button
              onClick={handleSingleConfirm}
              disabled={saving}
              className="inline-flex items-center gap-2 rounded-lg bg-indigo-500 px-4 py-2 text-sm font-medium text-white transition hover:bg-indigo-400 disabled:bg-indigo-500/30"
            >
              {saving && <Spinner />}
              {saving ? "Saving…" : "Confirm & Add Contact"}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

// ── Review view ────────────────────────────────────────────────────────────────

function ReviewView({
  contacts,
  currentIdx,
  slideDir,
  onBack,
  onNavigate,
  onUpdateCard,
  onConfirm,
  onSkip,
}: {
  contacts: ReviewContact[];
  currentIdx: number;
  slideDir: "forward" | "backward";
  onBack: () => void;
  onNavigate: (idx: number, dir: "forward" | "backward") => void;
  onUpdateCard: (idx: number, updates: Partial<ReviewContact>) => void;
  onConfirm: (idx: number) => boolean;
  onSkip: (idx: number) => void;
}) {
  const [emailError, setEmailError] = useState(false);
  const touchStartX = useRef(0);
  const handlersRef = useRef({
    confirm: () => {},
    prev: () => {},
    skip: () => {},
    back: () => {},
  });

  const contact = contacts[currentIdx];
  const confirmedCount = contacts.filter((c) => c.status === "confirmed").length;
  const isLastCard = currentIdx === contacts.length - 1;
  const pct = contacts.length > 0 ? (confirmedCount / contacts.length) * 100 : 0;

  function handleConfirmClick() {
    const ok = onConfirm(currentIdx);
    setEmailError(!ok);
  }

  // Update handlers ref on every render so the keydown listener sees current values
  handlersRef.current = {
    confirm: handleConfirmClick,
    prev: () => { if (currentIdx > 0) onNavigate(currentIdx - 1, "backward"); },
    skip: () => { setEmailError(false); onSkip(currentIdx); },
    back: onBack,
  };

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      const tag = (e.target as HTMLElement).tagName.toLowerCase();
      if (tag === "input" || tag === "textarea" || tag === "select") return;
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
  }, []);

  useEffect(() => {
    setEmailError(false);
  }, [currentIdx]);

  const year = extractYear(contact.notes);

  return (
    <div className="space-y-3">
      {/* ── Top bar ── */}
      <div className="space-y-1.5">
        <div className="flex items-center gap-3">
          <button
            onClick={onBack}
            className="shrink-0 rounded-lg border border-border px-3 py-1.5 text-xs text-fg-muted hover:text-fg transition"
          >
            ← Back to input
          </button>
          <div className="flex-1 space-y-1 min-w-0">
            <div className="h-1 rounded-full bg-surface-2 overflow-hidden">
              {/* Dynamic width — cannot be a static Tailwind class */}
              <div
                className="h-full bg-indigo-500 transition-all duration-500 rounded-full"
                style={{ width: `${pct}%` }}
              />
            </div>
            <div className="text-center text-xs text-fg-muted">
              Reviewing {currentIdx + 1} of {contacts.length}
            </div>
          </div>
          <button
            onClick={() => { setEmailError(false); onSkip(currentIdx); }}
            className="shrink-0 rounded-lg border border-border px-3 py-1.5 text-xs text-fg-muted hover:text-fg transition"
          >
            Skip →
          </button>
        </div>
        <p className="text-center text-xs text-fg-dim">
          Press Enter to confirm · ← → to navigate · Esc to go back
        </p>
      </div>

      {/* ── Animated card ── */}
      <div
        key={currentIdx}
        className={`mx-auto max-w-2xl ${slideDir === "forward" ? "slide-from-right" : "slide-from-left"}`}
        onTouchStart={(e) => { touchStartX.current = e.touches[0].clientX; }}
        onTouchEnd={(e) => {
          const delta = e.changedTouches[0].clientX - touchStartX.current;
          if (delta < -50) handleConfirmClick();
          else if (delta > 50 && currentIdx > 0) onNavigate(currentIdx - 1, "backward");
        }}
      >
        <div className="rounded-xl border border-border bg-[#1a1d2e] overflow-hidden flex divide-x divide-border">
          {/* Left panel — read-only identity */}
          <div className="w-2/5 shrink-0 p-4 space-y-3">
            <div>
              <div className="text-base font-bold text-fg leading-snug">{contact.name}</div>
              {contact.dartmouth && (
                <div className="mt-1.5 space-y-0.5">
                  <span className="inline-flex items-center gap-1 rounded-full bg-emerald-500/15 border border-emerald-500/20 px-2 py-0.5 text-xs text-emerald-300">
                    🎓 Tuck Alumni
                  </span>
                  {year && (
                    <div className="text-xs text-fg-dim pl-0.5">Class of {year}</div>
                  )}
                </div>
              )}
              {contact.missing_email && (
                <div className="mt-1.5">
                  <span className="inline-flex items-center gap-1 rounded-full bg-red-500/15 border border-red-500/20 px-2 py-0.5 text-xs text-red-300">
                    ⚠ No email — cannot import
                  </span>
                </div>
              )}
            </div>

            {contact.missing_email ? (
              <div>
                <input
                  type="email"
                  value={contact.email ?? ""}
                  onChange={(e) => {
                    const val = e.target.value;
                    onUpdateCard(currentIdx, {
                      email: val,
                      missing_email: !val.includes("@"),
                    });
                  }}
                  placeholder="Add email address"
                  className={`w-full rounded-lg border px-3 py-1.5 text-xs bg-surface text-fg placeholder-fg-dim focus:outline-none transition ${
                    emailError
                      ? "border-red-500 focus:border-red-400"
                      : "border-red-500/50 focus:border-red-400"
                  }`}
                />
                {emailError && (
                  <p className="mt-1 text-xs text-red-400">
                    Add an email address to confirm this contact
                  </p>
                )}
              </div>
            ) : (
              <div className="text-xs text-fg-muted truncate">{contact.email}</div>
            )}

            <div className="space-y-0.5">
              <div className="text-xs font-medium text-fg-muted">{contact.company}</div>
              <div className="text-xs text-fg-dim">{contact.role}</div>
            </div>

            <div className="border-t border-border/50 pt-2.5 space-y-1">
              <div className="text-xs uppercase tracking-wider text-fg-dim">
                Claude&apos;s hook
              </div>
              <div className="text-xs italic text-indigo-300 leading-relaxed">
                {contact.detail}
              </div>
            </div>
          </div>

          {/* Right panel — all editable */}
          <div className="flex-1 p-4 space-y-3">
            <div>
              <Label>Tier</Label>
              <div className="grid grid-cols-3 gap-1.5">
                {(
                  [
                    { v: 1, label: "1 — Dream" },
                    { v: 2, label: "2 — Strong Fit" },
                    { v: 3, label: "3 — Worth a Shot" },
                  ] as const
                ).map((t) => {
                  const active = (contact.tier ?? 2) === t.v;
                  return (
                    <button
                      key={t.v}
                      type="button"
                      onClick={() => onUpdateCard(currentIdx, { tier: t.v })}
                      className={`rounded-lg border py-2 text-xs transition ${
                        active
                          ? "border-indigo-500 bg-indigo-500/10 text-indigo-300"
                          : "border-border bg-surface text-fg-muted hover:border-border-strong"
                      }`}
                    >
                      {t.label}
                    </button>
                  );
                })}
              </div>
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
                      onClick={() => onUpdateCard(currentIdx, { mode: m })}
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
            </div>

            <div>
              <Label>Personalization hook</Label>
              <input
                type="text"
                value={contact.detail ?? ""}
                onChange={(e) => onUpdateCard(currentIdx, { detail: e.target.value })}
                placeholder="One specific thing to reference in the email"
                className="w-full rounded-lg border border-border bg-surface px-3 py-2 text-xs text-fg placeholder-fg-dim focus:border-indigo-500 focus:outline-none transition"
              />
            </div>

            <div>
              <Label>Resume Link</Label>
              <input
                type="text"
                value={contact.resume_url ?? ""}
                onChange={(e) => onUpdateCard(currentIdx, { resume_url: e.target.value })}
                placeholder="Paste Google Drive URL (optional)"
                className="w-full rounded-lg border border-border bg-surface px-3 py-2 text-xs text-fg placeholder-fg-dim focus:border-indigo-500 focus:outline-none transition"
              />
            </div>

            <div>
              <Label>Notes</Label>
              <input
                type="text"
                value={contact.notes ?? ""}
                onChange={(e) => onUpdateCard(currentIdx, { notes: e.target.value })}
                placeholder="Any extra context"
                className="w-full rounded-lg border border-border bg-surface px-3 py-2 text-xs text-fg placeholder-fg-dim focus:border-indigo-500 focus:outline-none transition"
              />
            </div>
          </div>
        </div>
      </div>

      {/* ── Bottom bar ── */}
      <div className="sticky bottom-0 rounded-xl border border-border bg-bg py-3 px-4">
        <div className="flex items-center justify-between gap-3">
          <button
            onClick={() => { if (currentIdx > 0) onNavigate(currentIdx - 1, "backward"); }}
            disabled={currentIdx === 0}
            className="rounded-lg border border-border px-4 py-2 text-sm text-fg-muted transition hover:text-fg disabled:opacity-30 disabled:cursor-not-allowed"
          >
            ← Previous
          </button>

          <div className="flex items-center gap-1.5 flex-wrap justify-center">
            {contacts.map((c, i) => {
              const isCurrent = i === currentIdx;
              const color =
                c.missing_email
                  ? "bg-red-500"
                  : c.status === "confirmed"
                  ? "bg-indigo-500"
                  : c.status === "skipped"
                  ? "bg-fg-dim"
                  : "border border-border-strong";
              return (
                <button
                  key={i}
                  type="button"
                  onClick={() =>
                    onNavigate(i, i > currentIdx ? "forward" : "backward")
                  }
                  className={`rounded-full transition-all ${color} ${
                    isCurrent ? "h-3 w-3 animate-pulse" : "h-2 w-2"
                  }`}
                  aria-label={`Go to contact ${i + 1}`}
                />
              );
            })}
          </div>

          <button
            onClick={handleConfirmClick}
            className="rounded-lg bg-indigo-500 px-4 py-2 text-sm font-medium text-white transition hover:bg-indigo-400"
          >
            {isLastCard ? "Confirm & Import →" : "Confirm & Next →"}
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Summary screen ─────────────────────────────────────────────────────────────

function SummaryScreen({
  contacts,
  onReviewAgain,
  onImport,
}: {
  contacts: ReviewContact[];
  onReviewAgain: () => void;
  onImport: (toImport: ReviewContact[]) => void;
}) {
  const confirmed = contacts.filter((c) => c.status === "confirmed");
  const skipped = contacts.filter((c) => c.status === "skipped");
  const noEmail = contacts.filter(
    (c) => c.missing_email && c.status !== "confirmed"
  );
  const dartmouthInConfirmed = confirmed.filter((c) => c.dartmouth);

  return (
    <div className="space-y-4 max-w-lg mx-auto">
      <h3 className="text-xl font-semibold text-fg text-center">Ready to import</h3>

      {confirmed.length > 15 && (
        <div className="rounded-lg border border-yellow-600/40 bg-yellow-900/20 px-4 py-3 text-sm text-yellow-300">
          Importing many contacts at once may affect email deliverability.
          Consider spreading across multiple days using the Tier system — the agent emails Tier 1 contacts first.
        </div>
      )}

      <div className="rounded-xl border border-border bg-surface-2 overflow-hidden divide-y divide-border">
        <div className="p-4 space-y-2">
          <div className="flex items-center gap-2">
            <span className="text-sm text-emerald-400">✓</span>
            <span className="text-sm font-medium text-fg">
              Confirmed ({confirmed.length} contacts)
            </span>
          </div>
          {confirmed.length === 0 ? (
            <div className="text-xs text-fg-dim pl-5">None confirmed yet</div>
          ) : (
            <ul className="pl-5 space-y-0.5">
              {confirmed.map((c, i) => (
                <li key={i} className="text-sm text-fg-muted">
                  {c.name}
                </li>
              ))}
            </ul>
          )}
        </div>

        {skipped.length > 0 && (
          <div className="p-4 space-y-2">
            <div className="flex items-center gap-2">
              <span className="text-sm text-fg-dim">⊘</span>
              <span className="text-sm font-medium text-fg-muted">
                Skipped ({skipped.length} contacts)
              </span>
            </div>
            <ul className="pl-5 space-y-0.5">
              {skipped.map((c, i) => (
                <li key={i} className="text-sm text-fg-dim">
                  {c.name}
                </li>
              ))}
            </ul>
          </div>
        )}

        {noEmail.length > 0 && (
          <div className="p-4 space-y-2">
            <div className="flex items-center gap-2">
              <span className="text-sm text-red-400">⚠</span>
              <span className="text-sm font-medium text-red-300">
                Missing email ({noEmail.length} contacts)
              </span>
            </div>
            <ul className="pl-5 space-y-0.5">
              {noEmail.map((c, i) => (
                <li key={i} className="text-sm text-red-300/70">
                  {c.name} — no email, not imported
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>

      {dartmouthInConfirmed.length > 0 && (
        <div className="rounded-lg border border-emerald-500/30 bg-emerald-500/10 px-4 py-3 text-sm text-emerald-300">
          🎓 {dartmouthInConfirmed.length} Tuck{" "}
          {dartmouthInConfirmed.length === 1 ? "alumnus" : "alumni"} detected —
          alumni tone will be applied automatically when the agent emails them
        </div>
      )}

      <div className="flex gap-3 pt-1">
        <button
          onClick={onReviewAgain}
          className="flex-1 rounded-lg border border-border px-4 py-2.5 text-sm text-fg-muted hover:text-fg transition"
        >
          ← Review Again
        </button>
        <button
          onClick={() => onImport(confirmed)}
          disabled={confirmed.length === 0}
          className="flex-1 rounded-lg bg-indigo-500 px-4 py-2.5 text-sm font-medium text-white transition hover:bg-indigo-400 disabled:bg-indigo-500/30 disabled:cursor-not-allowed"
        >
          Import {confirmed.length} Contact{confirmed.length !== 1 ? "s" : ""} →
        </button>
      </div>
    </div>
  );
}

// ── Import progress view ───────────────────────────────────────────────────────

function ImportProgressView({ results }: { results: ImportResult[] }) {
  return (
    <div className="space-y-4 max-w-lg mx-auto">
      <h3 className="text-xl font-semibold text-fg text-center">Importing contacts…</h3>
      <div className="rounded-xl border border-border bg-surface-2 overflow-hidden divide-y divide-border">
        {results.map((r, i) => (
          <div key={i} className="flex items-center gap-3 px-4 py-3">
            <span className="w-5 shrink-0 text-center text-sm">
              {r.status === "pending" ? "⏳" : r.status === "ok" ? "✓" : "✗"}
            </span>
            <span
              className={`flex-1 text-sm ${
                r.status === "error" ? "text-red-300" : "text-fg"
              }`}
            >
              {r.contact.name}
            </span>
            {r.status === "pending" && (
              <span className="text-xs text-fg-dim">importing…</span>
            )}
            {r.status === "ok" && (
              <span className="text-xs text-emerald-400">added</span>
            )}
            {r.status === "error" && (
              <span className="text-xs text-red-400 truncate max-w-[10rem]">
                {r.error ?? "failed"}
              </span>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

// ── Done screen ────────────────────────────────────────────────────────────────

function DoneScreen({
  results,
  onViewPipeline,
  onAddMore,
  onRetryFailed,
}: {
  results: ImportResult[];
  onViewPipeline: () => void;
  onAddMore: () => void;
  onRetryFailed: () => void;
}) {
  const okCount = results.filter((r) => r.status === "ok").length;
  const failedCount = results.filter((r) => r.status === "error").length;

  return (
    <div className="space-y-5 max-w-md mx-auto text-center py-4">
      <Confetti />

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
          The agent will email them tomorrow at 8am EST
        </p>
      </div>

      {failedCount > 0 && (
        <div className="rounded-lg border border-red-600/40 bg-red-900/20 px-4 py-3 text-sm text-red-300">
          {failedCount} contact{failedCount !== 1 ? "s" : ""} failed to import.{" "}
          <button onClick={onRetryFailed} className="underline hover:no-underline">
            Try again
          </button>
        </div>
      )}

      <div className="flex gap-3">
        <button
          onClick={onViewPipeline}
          className="flex-1 rounded-lg border border-border px-4 py-2.5 text-sm text-fg-muted hover:text-fg transition"
        >
          View Pipeline →
        </button>
        <button
          onClick={onAddMore}
          className="flex-1 rounded-lg bg-indigo-500 px-4 py-2.5 text-sm font-medium text-white transition hover:bg-indigo-400"
        >
          Add More Contacts
        </button>
      </div>
    </div>
  );
}

// ── Confetti ───────────────────────────────────────────────────────────────────

function Confetti() {
  const pieces = [
    { left: "8%",  color: "#6366f1", delay: "0ms"   },
    { left: "18%", color: "#22c55e", delay: "120ms"  },
    { left: "28%", color: "#f59e0b", delay: "60ms"   },
    { left: "38%", color: "#ec4899", delay: "180ms"  },
    { left: "50%", color: "#6366f1", delay: "30ms"   },
    { left: "62%", color: "#22c55e", delay: "150ms"  },
    { left: "72%", color: "#f59e0b", delay: "90ms"   },
    { left: "82%", color: "#ec4899", delay: "210ms"  },
    { left: "92%", color: "#6366f1", delay: "45ms"   },
  ];
  return (
    <div
      className="relative h-14 overflow-hidden pointer-events-none"
      aria-hidden="true"
    >
      {/* Positions and delays are computed per-piece — inline style is intentional */}
      {pieces.map((p, i) => (
        <div
          key={i}
          className="absolute top-0 h-2 w-2 rounded-sm"
          style={{
            left: p.left,
            backgroundColor: p.color,
            animation: `confetti-fall 1s ${p.delay} ease-out forwards`,
          }}
        />
      ))}
    </div>
  );
}

// ── Shared helpers ─────────────────────────────────────────────────────────────

function extractYear(notes: string | null | undefined): string | null {
  if (!notes) return null;
  const m = notes.match(/\b(19|20)\d{2}\b/);
  return m ? m[0] : null;
}

function Field({
  label,
  required,
  children,
}: {
  label: string;
  required?: boolean;
  children: React.ReactNode;
}) {
  return (
    <div>
      <Label required={required}>{label}</Label>
      {children}
    </div>
  );
}

function Spinner() {
  return (
    <svg
      className="h-4 w-4 animate-spin"
      viewBox="0 0 24 24"
      fill="none"
      aria-hidden="true"
    >
      <circle
        cx="12"
        cy="12"
        r="10"
        stroke="currentColor"
        strokeWidth="3"
        opacity="0.25"
      />
      <path
        d="M22 12a10 10 0 0 0-10-10"
        stroke="currentColor"
        strokeWidth="3"
        strokeLinecap="round"
      />
    </svg>
  );
}

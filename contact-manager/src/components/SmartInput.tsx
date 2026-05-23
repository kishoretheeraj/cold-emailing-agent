"use client";

import { useState } from "react";
import { supabase, resolveInsertError } from "@/lib/supabase";
import { US_STATES } from "@/lib/timezone";
import type { ExtractedContact, ReviewContact, BulkImportWindow } from "@/lib/types";
import { Label, TextInput, TextArea, ToggleSwitch, TierSelector } from "./Field";
import {
  Select,
  SelectTrigger,
  SelectValue,
  SelectContent,
  SelectItem,
} from "@/components/ui/Select";
import { ReviewFlow } from "./ReviewFlow";

// ── Main component ─────────────────────────────────────────────────────────────

export function SmartInput({
  onAdded,
  onError,
}: {
  onAdded: (window?: BulkImportWindow) => void;
  onError: (msg: string) => void;
}) {
  const [text, setText] = useState("");
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [preview, setPreview] = useState<ExtractedContact | null>(null);

  const [reviewContacts, setReviewContacts] = useState<ReviewContact[]>([]);
  const [reviewMode, setReviewMode] = useState(false);

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
          json.contacts as ExtractedContact[]
        ).map((c) => ({
          ...c,
          status: "pending" as const,
        }));
        setReviewContacts(contacts);
        setReviewMode(true);
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
        resume_url: preview.resume_url ?? null,
        state: preview.state ?? null,
        stage: "new",
        reply_status: "no_reply",
      };
      const { error } = await supabase.from("contacts").insert(row);
      if (error) throw new Error(await resolveInsertError(error, row.email as string ?? ""));
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

  function resetAll() {
    setText("");
    setPreview(null);
    setReviewMode(false);
    setReviewContacts([]);
  }

  // ── Bulk review mode ───────────────────────────────────────────────────────

  if (reviewMode) {
    return (
      <ReviewFlow
        contacts={reviewContacts}
        onUpdate={(index, updated) => {
          setReviewContacts((cs) =>
            cs.map((c, i) => (i === index ? updated : c))
          );
        }}
        onBack={() => {
          setReviewMode(false);
          setReviewContacts([]);
        }}
        onAddMore={resetAll}
        onAdded={onAdded}
        onError={onError}
      />
    );
  }

  // ── Input mode — single-contact flow ──────────────────────────────────────

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
          {loading ? "Extracting..." : "Extract with Claude"}
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

          <Field label="State">
            <Select
              value={preview.state ?? ""}
              onValueChange={(v) => updateSingle("state", v === "_none" ? null : (v || null))}
            >
              <SelectTrigger className="w-full">
                <SelectValue placeholder="Unknown / not US" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="_none">Unknown / not US</SelectItem>
                {US_STATES.map((s) => (
                  <SelectItem key={s.code} value={s.code}>
                    {s.code} - {s.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </Field>

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
              {saving ? "Saving..." : "Confirm & Add Contact"}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

// ── Local helpers ──────────────────────────────────────────────────────────────

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

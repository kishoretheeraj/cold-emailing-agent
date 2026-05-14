"use client";

import { useEffect, useState } from "react";
import { supabase } from "@/lib/supabase";
import {
  type Contact,
  type ReplyStatus,
  type BulkImportWindow,
  OUTREACH_STAGES,
  APPLIED_STAGES,
  REPLY_STATUSES,
} from "@/lib/types";
import { Label, TextInput, TextArea, ToggleSwitch, TierSelector } from "./Field";

// ── Status bar ────────────────────────────────────────────────────────────────

type Stats = {
  lastRun: string | null;
  lastRunStatus: "success" | "failure" | null;
  lastRunFailureReason: string | null;
  pipelineCount: number;
  draftsCount: number;
  errorsCount: number;
  promptsUpdatedToday: boolean;
};

function StatusBar({
  refreshKey,
}: {
  refreshKey: number;
}) {
  const [stats, setStats] = useState<Stats | null>(null);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const [lastRunRes, pipelineRes, draftsRes, errNotesRes, errStuckRes] =
          await Promise.all([
            supabase
              .from("agent_runs")
              .select("ran_at, status, failure_reason")
              .order("ran_at", { ascending: false })
              .limit(1),
            supabase
              .from("contacts")
              .select("*", { count: "exact", head: true })
              .eq("reply_status", "no_reply"),
            supabase
              .from("contacts")
              .select("*", { count: "exact", head: true })
              .like("stage", "%drafted%"),
            supabase
              .from("contacts")
              .select("*", { count: "exact", head: true })
              .ilike("notes", "%ERROR%"),
            supabase
              .from("contacts")
              .select("*", { count: "exact", head: true })
              .is("last_emailed", null)
              .neq("stage", "new"),
          ]);

        if (cancelled) return;

        const lastRunRow = (lastRunRes.data as {
          ran_at: string;
          status: "success" | "failure";
          failure_reason: string | null;
        }[] | null)?.[0] ?? null;
        const lastRun = lastRunRow?.ran_at ?? null;
        const lastRunStatus = lastRunRow?.status ?? null;
        const lastRunFailureReason = lastRunRow?.failure_reason ?? null;
        const pipelineCount = pipelineRes.count ?? 0;
        const draftsCount = draftsRes.count ?? 0;
        const errorsCount =
          (errNotesRes.count ?? 0) + (errStuckRes.count ?? 0);

        // Best-effort: check if any prompt was updated in the last 24 hours.
        let promptsUpdatedToday = false;
        try {
          const { data: pd } = await supabase
            .from("prompts")
            .select("updated_at")
            .order("updated_at", { ascending: false })
            .limit(1);
          const lu = (pd as { updated_at: string }[] | null)?.[0]?.updated_at;
          promptsUpdatedToday = lu
            ? Date.now() - new Date(lu).getTime() < 24 * 60 * 60 * 1000
            : false;
        } catch {
          // prompts table may not exist yet
        }

        setStats({ lastRun, lastRunStatus, lastRunFailureReason, pipelineCount, draftsCount, errorsCount, promptsUpdatedToday });
      } catch {
        // Stats are non-blocking — a failed fetch just leaves the bar hidden.
      }
    }
    load();
    return () => {
      cancelled = true;
    };
  }, [refreshKey]);

  if (!stats) return null;

  const staleAgent =
    stats.lastRun === null ||
    Date.now() - new Date(stats.lastRun).getTime() > 48 * 60 * 60 * 1000;

  return (
    <div className="space-y-2 mb-3">
      {stats.lastRunStatus === "failure" && (
        <div className="rounded-lg border border-red-600/40 bg-red-900/20 px-3 py-2 text-xs text-red-300">
          Last run failed
          {stats.lastRunFailureReason ? ` — ${stats.lastRunFailureReason}` : ""}
        </div>
      )}
      {staleAgent && stats.lastRunStatus !== "failure" && (
        <div className="rounded-lg border border-yellow-600/40 bg-yellow-900/20 px-3 py-2 text-xs text-yellow-300">
          Agent hasn't run in 2+ days — check GitHub Actions
        </div>
      )}
      {stats.promptsUpdatedToday && (
        <div className="rounded-lg border border-indigo-500/30 bg-indigo-500/10 px-3 py-2 text-xs text-indigo-300">
          Prompts updated today — next agent run will use new versions
        </div>
      )}
      <div className="flex flex-wrap gap-4 px-1 text-xs text-fg-muted">
        <span>
          Last run:{" "}
          <span className="text-fg">
            {stats.lastRun ? formatDate(stats.lastRun) : "never"}
          </span>
        </span>
        <span>
          Pipeline:{" "}
          <span className="text-fg">{stats.pipelineCount}</span>
        </span>
        <span>
          Drafts pending:{" "}
          <span className="text-fg">{stats.draftsCount}</span>
        </span>
        <span>
          Errors:{" "}
          <span
            className={
              stats.errorsCount > 0 ? "text-red-400" : "text-fg"
            }
          >
            {stats.errorsCount}
          </span>
        </span>
      </div>
    </div>
  );
}

type Props = {
  refreshKey: number;
  onError: (msg: string) => void;
  onUpdated: () => void;
  bulkImportWindow?: BulkImportWindow | null;
};

export function ContactsList({ refreshKey, onError, onUpdated, bulkImportWindow }: Props) {
  const [contacts, setContacts] = useState<Contact[] | null>(null);
  const [selected, setSelected] = useState<Contact | null>(null);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      const { data, error } = await supabase
        .from("contacts")
        .select("*")
        .order("created_at", { ascending: false, nullsFirst: false })
        .limit(20);
      if (cancelled) return;
      if (error) {
        onError(`Failed to load contacts: ${error.message}`);
        setContacts([]);
        return;
      }
      setContacts((data ?? []) as Contact[]);
    }
    load();
    return () => {
      cancelled = true;
    };
  }, [refreshKey, onError]);

  return (
    <>
      <StatusBar refreshKey={refreshKey} />

      {contacts === null ? (
        <div className="rounded-xl border border-border bg-surface p-10 text-center text-sm text-fg-muted">
          Loading…
        </div>
      ) : contacts.length === 0 ? (
        <div className="rounded-xl border border-border bg-surface p-10 text-center">
          <p className="text-sm text-fg-muted">
            No contacts yet — add your first one above
          </p>
        </div>
      ) : (
        <div className="rounded-xl border border-border bg-surface overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-surface-2 text-left text-xs uppercase tracking-wider text-fg-muted">
                <tr>
                  <th className="px-4 py-3 font-medium">Name</th>
                  <th className="px-4 py-3 font-medium">Company</th>
                  <th className="px-4 py-3 font-medium">Mode</th>
                  <th className="px-4 py-3 font-medium">Stage</th>
                  <th className="px-4 py-3 font-medium">Reply</th>
                  <th className="px-4 py-3 font-medium hidden sm:table-cell">
                    Added
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {contacts.map((c) => (
                  <tr
                    key={c.id}
                    onClick={() => setSelected(c)}
                    className="cursor-pointer hover:bg-surface-2 transition"
                  >
                    <td className="px-4 py-3 text-fg">
                      {c.name ?? "—"}
                      {isBulkContact(c, bulkImportWindow) && (
                        <span
                          title={
                            c.created_at
                              ? new Date(c.created_at).toISOString()
                              : "Bulk import"
                          }
                          className="ml-1.5 inline-flex items-center rounded px-1 py-0.5 text-xs bg-surface-2 text-fg-dim border border-border cursor-default"
                        >
                          Bulk
                        </span>
                      )}
                    </td>
                    <td className="px-4 py-3 text-fg-muted">{c.company ?? "—"}</td>
                    <td className="px-4 py-3">
                      <ModePill mode={c.mode} />
                    </td>
                    <td className="px-4 py-3">
                      <StagePill stage={c.stage} />
                    </td>
                    <td className="px-4 py-3">
                      <ReplyPill status={c.reply_status} />
                    </td>
                    <td className="px-4 py-3 text-fg-dim hidden sm:table-cell">
                      {formatDate(c.created_at)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {selected && (
        <SidePanel
          contact={selected}
          onClose={() => setSelected(null)}
          onSaved={(updated) => {
            setContacts((cs) =>
              cs ? cs.map((c) => (c.id === updated.id ? updated : c)) : cs
            );
            setSelected(updated);
            onUpdated();
          }}
          onError={onError}
        />
      )}
    </>
  );
}

// ── Bulk badge detection ──────────────────────────────────────────────────────

function isBulkContact(
  c: Contact,
  window: BulkImportWindow | null | undefined
): boolean {
  if (!window || !c.created_at) return false;
  const t = new Date(c.created_at).getTime();
  return t > window.startedAt - 5000 && t < window.endedAt + 5000;
}

// ── Pills ─────────────────────────────────────────────────────────────────────

function ModePill({ mode }: { mode: Contact["mode"] }) {
  if (!mode) return <span className="text-fg-dim">—</span>;
  return (
    <span className="rounded-md border border-border-strong bg-surface-2 px-2 py-0.5 text-xs capitalize text-fg-muted">
      {mode}
    </span>
  );
}

function stageStyles(stage: string | null) {
  if (!stage)
    return { color: "var(--color-stage-new)", label: "—" };
  if (stage === "new") return { color: "var(--color-stage-new)", label: stage };
  if (stage === "closed")
    return { color: "var(--color-fg-dim)", label: stage };
  if (stage.includes("drafted"))
    return { color: "var(--color-stage-drafted)", label: stage };
  if (stage.includes("sent"))
    return { color: "var(--color-stage-sent)", label: stage };
  return { color: "var(--color-fg-muted)", label: stage };
}

function StagePill({ stage }: { stage: string | null }) {
  const { color, label } = stageStyles(stage);
  return (
    <span
      className="inline-flex items-center gap-1.5 rounded-md px-2 py-0.5 text-xs font-medium"
      style={{ color, backgroundColor: `color-mix(in srgb, ${color} 12%, transparent)` }}
    >
      <span
        className="h-1.5 w-1.5 rounded-full"
        style={{ backgroundColor: color }}
      />
      {label}
    </span>
  );
}

function ReplyPill({ status }: { status: ReplyStatus | null }) {
  if (!status || status === "no_reply") {
    return <span className="text-xs text-fg-dim">no reply</span>;
  }
  if (status === "dead") {
    return (
      <span className="rounded-md bg-red-900/40 px-2 py-0.5 text-xs text-red-300/80">
        dead
      </span>
    );
  }
  return (
    <span className="rounded-md bg-emerald-500/15 px-2 py-0.5 text-xs text-emerald-300">
      {status.replace("_", " ")}
    </span>
  );
}

function formatDate(iso?: string) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
  });
}

// ── Side panel ────────────────────────────────────────────────────────────────

function SidePanel({
  contact,
  onClose,
  onSaved,
  onError,
}: {
  contact: Contact;
  onClose: () => void;
  onSaved: (c: Contact) => void;
  onError: (msg: string) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<Contact>(contact);
  const [saving, setSaving] = useState(false);

  // Sync all state when a different contact is selected
  useEffect(() => {
    setEditing(false);
    setDraft(contact);
  }, [contact.id]); // eslint-disable-line react-hooks/exhaustive-deps

  function updateDraft<K extends keyof Contact>(key: K, val: Contact[K]) {
    setDraft((d) => ({ ...d, [key]: val }));
  }

  function cancelEdit() {
    setDraft(contact);
    setEditing(false);
  }

  const stages =
    (editing ? draft.mode : contact.mode) === "applied"
      ? APPLIED_STAGES
      : OUTREACH_STAGES;

  async function save() {
    setSaving(true);
    try {
      const { data, error } = await supabase
        .from("contacts")
        .update({
          name: draft.name,
          email: draft.email,
          company: draft.company,
          role: draft.role,
          detail: draft.detail,
          tier: draft.tier,
          mode: draft.mode,
          dartmouth: draft.dartmouth,
          notes: draft.notes,
          resume_url: draft.resume_url ?? null,
          followup_date: draft.followup_date,
          stage: draft.stage,
          reply_status: draft.reply_status,
          job_title: draft.job_title,
          applied_date: draft.applied_date,
          job_description: draft.job_description,
        })
        .eq("id", contact.id)
        .select()
        .single();
      if (error) throw new Error(error.message);
      onSaved(data as Contact);
      setEditing(false);
    } catch (err) {
      onError(err instanceof Error ? err.message : "update failed");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="fixed inset-0 z-40 flex">
      <div
        className="flex-1 bg-black/50 backdrop-blur-sm"
        onClick={onClose}
      />
      <aside className="w-full sm:w-[440px] bg-surface border-l border-border overflow-y-auto p-6 space-y-5">
        {/* ── Header ── */}
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-semibold text-fg">
            {editing ? (draft.name ?? "Contact") : (contact.name ?? "Contact")}
          </h2>
          <div className="flex items-center gap-3">
            {editing ? (
              <button
                onClick={cancelEdit}
                className="text-sm text-fg-muted hover:text-fg"
              >
                Cancel
              </button>
            ) : (
              <button
                onClick={() => setEditing(true)}
                className="text-sm text-fg-muted hover:text-fg"
              >
                Edit
              </button>
            )}
            <button
              onClick={onClose}
              className="text-fg-muted hover:text-fg text-sm"
              aria-label="Close panel"
            >
              ✕
            </button>
          </div>
        </div>

        {editing ? (
          /* ── Edit mode ── */
          <div className="space-y-4">
            <div className="grid grid-cols-1 gap-4">
              <PanelField label="Name">
                <TextInput
                  value={draft.name ?? ""}
                  onChange={(e) => updateDraft("name", e.target.value)}
                />
              </PanelField>
              <PanelField label="Email">
                <TextInput
                  type="email"
                  value={draft.email ?? ""}
                  onChange={(e) => updateDraft("email", e.target.value)}
                />
              </PanelField>
              <PanelField label="Company">
                <TextInput
                  value={draft.company ?? ""}
                  onChange={(e) => updateDraft("company", e.target.value)}
                />
              </PanelField>
              <PanelField label="Role">
                <TextInput
                  value={draft.role ?? ""}
                  onChange={(e) => updateDraft("role", e.target.value)}
                />
              </PanelField>
            </div>

            <PanelField label="Personalization detail">
              <TextArea
                value={draft.detail ?? ""}
                onChange={(e) => updateDraft("detail", e.target.value)}
                rows={3}
              />
            </PanelField>

            <div>
              <Label>Tier</Label>
              <TierSelector
                value={draft.tier ?? 2}
                onChange={(v) => updateDraft("tier", v)}
              />
            </div>

            <div>
              <Label>Mode</Label>
              <div className="grid grid-cols-2 gap-2">
                {(["outreach", "applied"] as const).map((m) => {
                  const active = (draft.mode ?? "outreach") === m;
                  return (
                    <button
                      key={m}
                      type="button"
                      onClick={() => updateDraft("mode", m)}
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

            <ToggleSwitch
              on={draft.dartmouth ?? false}
              onChange={(v) => updateDraft("dartmouth", v)}
              label="Dartmouth / Tuck / Thayer / Irving connection"
            />

            {draft.mode === "applied" && (
              <>
                <PanelField label="Job Title">
                  <TextInput
                    value={draft.job_title ?? ""}
                    onChange={(e) => updateDraft("job_title", e.target.value)}
                  />
                </PanelField>
                <PanelField label="Applied Date">
                  <TextInput
                    type="date"
                    value={draft.applied_date ?? ""}
                    onChange={(e) => updateDraft("applied_date", e.target.value)}
                  />
                </PanelField>
                <PanelField label="Job Description">
                  <TextArea
                    value={draft.job_description ?? ""}
                    onChange={(e) => updateDraft("job_description", e.target.value)}
                    rows={5}
                  />
                </PanelField>
              </>
            )}

            <PanelField label="Followup Date">
              <TextInput
                type="date"
                value={draft.followup_date ?? ""}
                onChange={(e) => updateDraft("followup_date", e.target.value)}
              />
            </PanelField>

            <PanelField label="Resume Link">
              <TextInput
                value={draft.resume_url ?? ""}
                onChange={(e) => updateDraft("resume_url", e.target.value)}
              />
            </PanelField>

            <PanelField label="Notes">
              <TextArea
                value={draft.notes ?? ""}
                onChange={(e) => updateDraft("notes", e.target.value)}
                rows={2}
              />
            </PanelField>

            <div className="rounded-lg border border-border bg-surface-2 p-4 space-y-3">
              <h3 className="text-xs uppercase tracking-wider text-fg-muted">
                Status
              </h3>
              <div>
                <label className="block text-xs text-fg-muted mb-1">Stage</label>
                <select
                  value={draft.stage ?? "new"}
                  onChange={(e) => updateDraft("stage", e.target.value)}
                  className="w-full rounded-md border border-border bg-surface px-3 py-2 text-sm text-fg"
                >
                  {stages.map((s) => (
                    <option key={s} value={s}>{s}</option>
                  ))}
                </select>
              </div>
              <div>
                <label className="block text-xs text-fg-muted mb-1">Reply Status</label>
                <select
                  value={draft.reply_status ?? "no_reply"}
                  onChange={(e) =>
                    updateDraft("reply_status", e.target.value as ReplyStatus)
                  }
                  className="w-full rounded-md border border-border bg-surface px-3 py-2 text-sm text-fg"
                >
                  {REPLY_STATUSES.map((s) => (
                    <option key={s} value={s}>{s}</option>
                  ))}
                </select>
              </div>
            </div>

            <div className="flex gap-3 pt-1">
              <button
                onClick={cancelEdit}
                className="flex-1 rounded-md border border-border px-3 py-2 text-sm text-fg-muted hover:text-fg transition"
              >
                Cancel
              </button>
              <button
                onClick={save}
                disabled={saving}
                className="flex-1 rounded-md bg-indigo-500 px-3 py-2 text-sm font-medium text-white transition hover:bg-indigo-400 disabled:bg-indigo-500/30"
              >
                {saving ? "Saving…" : "Save Changes"}
              </button>
            </div>
          </div>
        ) : (
          /* ── View mode (unchanged) ── */
          <>
            <Detail label="Email" value={contact.email} />
            <Detail label="Company" value={contact.company} />
            <Detail label="Role" value={contact.role} />
            <Detail label="Mode" value={contact.mode} />
            <Detail label="Tier" value={contact.tier?.toString() ?? null} />
            <Detail
              label="Dartmouth"
              value={contact.dartmouth ? "Yes" : "No"}
            />
            <Detail label="Detail" value={contact.detail} multiline />
            {contact.mode === "applied" && (
              <>
                <Detail label="Job Title" value={contact.job_title} />
                <Detail label="Applied Date" value={contact.applied_date} />
                <Detail
                  label="Job Description"
                  value={contact.job_description}
                  multiline
                />
              </>
            )}
            <Detail label="Followup Date" value={contact.followup_date} />
            <Detail label="Notes" value={contact.notes} multiline />

            <div className="rounded-lg border border-border bg-surface-2 p-4 space-y-3">
              <h3 className="text-xs uppercase tracking-wider text-fg-muted">
                Update Status
              </h3>
              <div>
                <label className="block text-xs text-fg-muted mb-1">Stage</label>
                <select
                  value={contact.stage ?? "new"}
                  onChange={(e) => {
                    // Quick status update without entering full edit mode
                    supabase
                      .from("contacts")
                      .update({ stage: e.target.value })
                      .eq("id", contact.id)
                      .select()
                      .single()
                      .then(({ data, error }) => {
                        if (!error && data) onSaved(data as Contact);
                        else if (error) onError(error.message);
                      });
                  }}
                  className="w-full rounded-md border border-border bg-surface px-3 py-2 text-sm text-fg"
                >
                  {stages.map((s) => (
                    <option key={s} value={s}>{s}</option>
                  ))}
                </select>
              </div>
              <div>
                <label className="block text-xs text-fg-muted mb-1">Reply Status</label>
                <select
                  value={contact.reply_status ?? "no_reply"}
                  onChange={(e) => {
                    supabase
                      .from("contacts")
                      .update({ reply_status: e.target.value })
                      .eq("id", contact.id)
                      .select()
                      .single()
                      .then(({ data, error }) => {
                        if (!error && data) onSaved(data as Contact);
                        else if (error) onError(error.message);
                      });
                  }}
                  className="w-full rounded-md border border-border bg-surface px-3 py-2 text-sm text-fg"
                >
                  {REPLY_STATUSES.map((s) => (
                    <option key={s} value={s}>{s}</option>
                  ))}
                </select>
              </div>
            </div>
          </>
        )}
      </aside>
    </div>
  );
}

function PanelField({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <Label>{label}</Label>
      {children}
    </div>
  );
}

function Detail({
  label,
  value,
  multiline,
}: {
  label: string;
  value: string | null | undefined;
  multiline?: boolean;
}) {
  return (
    <div>
      <div className="text-xs uppercase tracking-wider text-fg-muted">
        {label}
      </div>
      <div
        className={`mt-1 text-sm text-fg ${
          multiline ? "whitespace-pre-wrap" : ""
        }`}
      >
        {value && value.toString().trim() ? value : "—"}
      </div>
    </div>
  );
}

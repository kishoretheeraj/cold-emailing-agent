"use client";

import { useEffect, useRef, useState, useCallback } from "react";
import { toast } from "sonner";
import { supabase } from "@/lib/supabase";
import { QUEUE_STAGES, STAGE_TRANSITIONS } from "@/lib/cadence";
import { highlight } from "@/lib/personalization";
import {
  formatLocalTime,
  getTimezoneLabel,
  getTimezoneDistribution,
  ianaToTimezoneLabel,
} from "@/lib/timezone";
import { Badge } from "@/components/ui/Badge";
import { Tooltip } from "@/components/ui/Tooltip";
import { TextInput, TextArea, ToggleSwitch } from "@/components/Field";
import type { Contact, DraftHistory, AgentEvent } from "@/lib/types";

// ── Types ──────────────────────────────────────────────────────────────────────

type PendingEntry = {
  timer: ReturnType<typeof setTimeout>;
  toastId: string | number;
  originalIndex: number;
  kind: "send" | "dead";
};

type Filters = {
  tiers: number[];
  stages: string[];
  dartmouthOnly: boolean;
};

type CriticSignal = {
  score: number;
  verdict: string;
  retried: boolean;
} | null;

// ── Helpers ────────────────────────────────────────────────────────────────────

function stageBadgeVariant(
  stage: string
): "indigo" | "amber" | "emerald" | "red" | "muted" | "default" {
  if (stage.includes("first_touch")) return "indigo";
  if (stage.includes("followup1")) return "amber";
  if (stage.includes("followup2")) return "emerald";
  if (stage.includes("breakup")) return "red";
  if (stage.includes("applied_intro")) return "indigo";
  if (stage.includes("applied_followup")) return "amber";
  return "default";
}

function tierBadgeVariant(
  tier: number | null
): "indigo" | "emerald" | "amber" | "default" {
  if (tier === 1) return "indigo";
  if (tier === 2) return "emerald";
  if (tier === 3) return "amber";
  return "default";
}

function stageLabel(stage: string): string {
  return stage.replace(/_drafted$/, "").replace(/_/g, " ");
}

function relativeTime(ts: string): string {
  const diff = Date.now() - new Date(ts).getTime();
  const h = Math.floor(diff / 3_600_000);
  if (h < 1) return "just now";
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

// ── Main component ─────────────────────────────────────────────────────────────

export function QueuePage() {
  const [contacts, setContacts] = useState<Contact[]>([]);
  const [draftsByContactId, setDraftsByContactId] = useState<
    Record<string, DraftHistory>
  >({});
  const [criticByContactId, setCriticByContactId] = useState<
    Record<string, CriticSignal>
  >({});
  const [focusedIndex, setFocusedIndex] = useState(0);
  const [editMode, setEditMode] = useState<"idle" | "quickfix">("idle");
  const [editSubject, setEditSubject] = useState("");
  const [editBody, setEditBody] = useState("");
  const [skippedIds, setSkippedIds] = useState<Set<string>>(new Set());
  const [filters, setFilters] = useState<Filters>({
    tiers: [],
    stages: [],
    dartmouthOnly: false,
  });
  const [showShortcuts, setShowShortcuts] = useState(false);
  const [now, setNow] = useState(() => new Date());

  // pendingSends: navigating away cancels pending sends (deliberate trade-off)
  const pendingSends = useRef<Map<string, PendingEntry>>(new Map());
  const lastGPress = useRef<number>(0);

  // ── Data fetch ───────────────────────────────────────────────────────────────

  const fetchData = useCallback(async () => {
    const { data: contactData } = await supabase
      .from("contacts")
      .select("*")
      .in("stage", [...QUEUE_STAGES])
      .is("deleted_at", null)
      .order("tier", { ascending: true })
      .order("created_at", { ascending: false });

    const fetched = (contactData as Contact[]) ?? [];
    setContacts(fetched);

    if (fetched.length === 0) {
      setDraftsByContactId({});
      setCriticByContactId({});
      return;
    }

    const ids = fetched.map((c) => c.id);

    const [{ data: draftData }, { data: eventData }] = await Promise.all([
      supabase
        .from("draft_history")
        .select("*")
        .in("contact_id", ids)
        .is("sent_body", null)
        .order("drafted_at", { ascending: false }),
      supabase
        .from("agent_events")
        .select("*")
        .in("contact_id", ids)
        .eq("event_type", "critic")
        .order("started_at", { ascending: false }),
    ]);

    // Keep latest draft per contact
    const drafts: Record<string, DraftHistory> = {};
    for (const d of (draftData as DraftHistory[]) ?? []) {
      if (!drafts[String(d.contact_id)]) {
        drafts[String(d.contact_id)] = d;
      }
    }
    setDraftsByContactId(drafts);

    // Keep latest critic event per contact
    const critics: Record<string, CriticSignal> = {};
    for (const e of (eventData as AgentEvent[]) ?? []) {
      const cid = String(e.contact_id);
      if (!critics[cid] && e.metadata) {
        critics[cid] = {
          score: e.metadata.score as number,
          verdict: e.metadata.verdict as string,
          retried: Boolean(e.metadata.retried),
        };
      }
    }
    setCriticByContactId(critics);
  }, []);

  // Preserve focus by contact_id across auto-refresh
  const focusedContactId = useRef<string | null>(null);

  useEffect(() => {
    fetchData();
    const id = setInterval(fetchData, 30_000);
    return () => clearInterval(id);
  }, [fetchData]);

  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 60_000);
    return () => clearInterval(id);
  }, []);

  // ── Filtered contacts ────────────────────────────────────────────────────────

  const visible = contacts.filter((c) => {
    if (skippedIds.has(c.id)) return false;
    if (pendingSends.current.has(c.id)) return false;
    if (filters.tiers.length > 0 && !filters.tiers.includes(c.tier ?? -1))
      return false;
    if (filters.stages.length > 0 && !filters.stages.includes(c.stage ?? ""))
      return false;
    if (filters.dartmouthOnly && !c.dartmouth) return false;
    return true;
  });

  // Restore focus by id after refresh
  useEffect(() => {
    if (visible.length === 0) return;
    const targetId = focusedContactId.current;
    if (targetId) {
      const idx = visible.findIndex((c) => c.id === targetId);
      if (idx !== -1) {
        setFocusedIndex(idx);
        return;
      }
    }
    setFocusedIndex((prev) => Math.min(prev, visible.length - 1));
  }, [visible.length]); // eslint-disable-line react-hooks/exhaustive-deps

  // Track focused contact_id
  useEffect(() => {
    focusedContactId.current = visible[focusedIndex]?.id ?? null;
  }, [focusedIndex, visible]);

  const focused = visible[focusedIndex] ?? null;
  const focusedDraft = focused ? draftsByContactId[focused.id] ?? null : null;
  const focusedCritic = focused ? criticByContactId[focused.id] ?? null : null;

  // ── Count helpers for filters ────────────────────────────────────────────────

  const tierCounts: Record<number, number> = {};
  const stageCounts: Record<string, number> = {};
  for (const c of contacts.filter((c) => !skippedIds.has(c.id))) {
    if (c.tier != null) tierCounts[c.tier] = (tierCounts[c.tier] ?? 0) + 1;
    if (c.stage) stageCounts[c.stage] = (stageCounts[c.stage] ?? 0) + 1;
  }

  // ── Approve and send ─────────────────────────────────────────────────────────

  const onApprove = useCallback(
    (contact: Contact, idx: number) => {
      if (pendingSends.current.has(contact.id)) return;

      setFocusedIndex((prev) =>
        idx < visible.length - 1 ? idx : Math.max(0, idx - 1)
      );

      const toastId = toast(`Sending to ${contact.name ?? "contact"}…`, {
        duration: 5000,
        action: {
          label: "Undo",
          onClick: () => onUndo(contact.id, idx),
        },
      });

      const timer = setTimeout(async () => {
        pendingSends.current.delete(contact.id);
        try {
          const res = await fetch("/api/send-draft", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ contact_id: contact.id }),
          });
          if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            throw new Error((err as { error?: string }).error ?? `HTTP ${res.status}`);
          }
          toast.success(`Sent to ${contact.name ?? "contact"}`);
          fetchData();
        } catch (err) {
          toast.error(
            `Send failed: ${err instanceof Error ? err.message : "Unknown error"}`
          );
          setContacts((prev) => {
            const next = [...prev];
            next.splice(idx, 0, contact);
            return next;
          });
        }
      }, 5000);

      pendingSends.current.set(contact.id, {
        timer,
        toastId,
        originalIndex: idx,
        kind: "send",
      });

      // Force re-filter to hide the row
      setContacts((prev) => [...prev]);
    },
    [visible.length, fetchData] // eslint-disable-line react-hooks/exhaustive-deps
  );

  const onUndo = useCallback((contactId: string, originalIndex: number) => {
    const entry = pendingSends.current.get(contactId);
    if (!entry) return;
    clearTimeout(entry.timer);
    toast.dismiss(entry.toastId);
    pendingSends.current.delete(contactId);
    toast.info("Send canceled");
    setContacts((prev) => [...prev]); // trigger re-filter
    setFocusedIndex(originalIndex);
  }, []);

  // ── Mark dead ────────────────────────────────────────────────────────────────

  const onMarkDead = useCallback(
    (contact: Contact, idx: number) => {
      if (pendingSends.current.has(contact.id)) return;

      setFocusedIndex((prev) =>
        idx < visible.length - 1 ? idx : Math.max(0, idx - 1)
      );

      const toastId = toast(
        `Marked ${contact.name ?? "contact"} dead — Undo (5s)`,
        {
          duration: 5000,
          action: {
            label: "Undo",
            onClick: () => onUndo(contact.id, idx),
          },
        }
      );

      const timer = setTimeout(async () => {
        pendingSends.current.delete(contact.id);
        try {
          await supabase
            .from("contacts")
            .update({ reply_status: "dead" })
            .eq("id", contact.id);
          fetchData();
        } catch {
          toast.error("Failed to mark dead");
          setContacts((prev) => {
            const next = [...prev];
            next.splice(idx, 0, contact);
            return next;
          });
        }
      }, 5000);

      pendingSends.current.set(contact.id, {
        timer,
        toastId,
        originalIndex: idx,
        kind: "dead",
      });

      setContacts((prev) => [...prev]);
    },
    [visible.length, fetchData, onUndo] // eslint-disable-line react-hooks/exhaustive-deps
  );

  // ── Quick Fix ────────────────────────────────────────────────────────────────

  const openQuickFix = useCallback(() => {
    if (!focused || !focusedDraft) return;
    setEditSubject(focusedDraft.subject ?? "");
    setEditBody(focusedDraft.body ?? "");
    setEditMode("quickfix");
  }, [focused, focusedDraft]);

  const onSaveAndSend = useCallback(async () => {
    if (!focused || !focusedDraft) return;
    try {
      const res = await fetch("/api/update-draft", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          contact_id: focused.id,
          subject: editSubject,
          body: editBody,
        }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setEditMode("idle");
      onApprove(focused, focusedIndex);
    } catch (err) {
      toast.error(
        `Update failed: ${err instanceof Error ? err.message : "Unknown error"}`
      );
    }
  }, [focused, focusedDraft, editSubject, editBody, focusedIndex, onApprove]);

  // ── Unmount: abort all pending sends ────────────────────────────────────────
  useEffect(() => {
    return () => {
      for (const entry of pendingSends.current.values()) {
        clearTimeout(entry.timer);
      }
    };
  }, []);

  // ── Keyboard handler ─────────────────────────────────────────────────────────

  useEffect(() => {
    function handler(e: KeyboardEvent) {
      const tag = (document.activeElement?.tagName ?? "").toUpperCase();
      const isEditable =
        tag === "INPUT" ||
        tag === "TEXTAREA" ||
        tag === "SELECT" ||
        (document.activeElement as HTMLElement)?.isContentEditable;
      if (isEditable) return;

      const len = visible.length;

      if (e.key === "j" || e.key === "ArrowDown") {
        e.preventDefault();
        setFocusedIndex((prev) => Math.min(len - 1, prev + 1));
      } else if (e.key === "k" || e.key === "ArrowUp") {
        e.preventDefault();
        setFocusedIndex((prev) => Math.max(0, prev - 1));
      } else if (e.key === "g") {
        const now = Date.now();
        if (now - lastGPress.current < 500) {
          setFocusedIndex(0);
          lastGPress.current = 0;
        } else {
          lastGPress.current = now;
        }
      } else if (e.key === "G") {
        setFocusedIndex(Math.max(0, len - 1));
      } else if (e.key === "e" && focused && focusedDraft?.gmail_draft_id) {
        onApprove(focused, focusedIndex);
      } else if (e.key === "E") {
        openQuickFix();
      } else if (e.key === "o" && focusedDraft?.gmail_draft_id) {
        window.open(
          `https://mail.google.com/mail/u/0/#drafts?compose=${focusedDraft.gmail_draft_id}`,
          "_blank"
        );
      } else if (e.key === "x" && focused) {
        setSkippedIds((prev) => new Set([...prev, focused.id]));
      } else if (e.key === "D" && focused) {
        onMarkDead(focused, focusedIndex);
      } else if (e.key === "1") {
        setFilters((f) => ({
          ...f,
          tiers: f.tiers.includes(1)
            ? f.tiers.filter((t) => t !== 1)
            : [...f.tiers, 1],
        }));
      } else if (e.key === "2") {
        setFilters((f) => ({
          ...f,
          tiers: f.tiers.includes(2)
            ? f.tiers.filter((t) => t !== 2)
            : [...f.tiers, 2],
        }));
      } else if (e.key === "3") {
        setFilters((f) => ({
          ...f,
          tiers: f.tiers.includes(3)
            ? f.tiers.filter((t) => t !== 3)
            : [...f.tiers, 3],
        }));
      } else if (e.key === "?") {
        setShowShortcuts((v) => !v);
      } else if (e.key === "Escape") {
        if (editMode === "quickfix") setEditMode("idle");
        else if (showShortcuts) setShowShortcuts(false);
        else
          setFilters({ tiers: [], stages: [], dartmouthOnly: false });
      }
    }

    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [
    visible,
    focused,
    focusedIndex,
    focusedDraft,
    editMode,
    showShortcuts,
    onApprove,
    onMarkDead,
    openQuickFix,
  ]);

  // ── Critic render helper ─────────────────────────────────────────────────────

  function renderCritic(contact: Contact, critic: CriticSignal): string {
    if ((contact.tier ?? 99) > 1) return "n/a (T2+)";
    if (!critic) return "—";
    const regen = critic.retried ? " (1 regen)" : "";
    return `${critic.score}/21 ✓${regen}`;
  }

  // ── Personalization summary ──────────────────────────────────────────────────

  function personalizationSummary(contact: Contact, body: string): string {
    const parts: string[] = [];
    if (body && contact.name?.split(/\s+/)[0]) {
      const first = contact.name.split(/\s+/)[0];
      if (body.toLowerCase().includes(first.toLowerCase())) parts.push("Name");
    }
    if (body && contact.company && body.toLowerCase().includes(contact.company.toLowerCase())) {
      parts.push("Company");
    }
    if (body && /\bT'\d{2}\b/.test(body)) parts.push("Alumni hook");
    if (body && /\b(Dartmouth|Tuck|Thayer)\b/i.test(body)) parts.push("Dartmouth");
    return parts.length > 0 ? parts.join(", ") : "None detected";
  }

  // ── Render ───────────────────────────────────────────────────────────────────

  const skippedCount = skippedIds.size;
  const activeFilterCount =
    filters.tiers.length +
    filters.stages.length +
    (filters.dartmouthOnly ? 1 : 0);

  const senderIana = Intl.DateTimeFormat().resolvedOptions().timeZone;
  const senderLabel = ianaToTimezoneLabel(senderIana) ?? senderIana;
  const senderTime = new Intl.DateTimeFormat("en-US", {
    hour: "numeric",
    minute: "2-digit",
    timeZone: senderIana,
  }).format(now);
  const tzDistribution = getTimezoneDistribution(contacts.map((c) => c.state ?? null));

  return (
    <div className="flex h-[calc(100vh-64px)] overflow-hidden">
      {/* ── Left rail ──────────────────────────────────────────────────── */}
      <aside className="w-[220px] shrink-0 border-r border-border flex flex-col overflow-y-auto">
        <div className="px-4 py-4 border-b border-border">
          <div className="flex items-center justify-between">
            <h2 className="text-xs font-semibold uppercase tracking-wider text-fg-muted">
              Queue
            </h2>
            <span className="rounded-full bg-indigo-500/20 border border-indigo-500/40 px-2 py-0.5 text-xs text-indigo-300">
              {visible.length}
            </span>
          </div>
          <p className="text-xs text-fg-dim mt-1">
            Your time: {senderTime} {senderLabel}
            {tzDistribution.length > 0 && (
              <> · {tzDistribution.map((d) => `${d.count} ${d.label}`).join(" · ")}</>
            )}
          </p>
          {skippedCount > 0 && (
            <p className="text-xs text-fg-dim mt-0.5">
              ({skippedCount} skipped this session)
            </p>
          )}
        </div>

        <div className="flex-1 px-4 py-4 space-y-5">
          {/* Tier filter */}
          <div>
            <p className="text-xs uppercase tracking-wider text-fg-dim mb-2">
              Tier
            </p>
            <div className="flex flex-wrap gap-1.5">
              {([1, 2, 3] as const).map((t) => (
                <button
                  key={t}
                  type="button"
                  onClick={() =>
                    setFilters((f) => ({
                      ...f,
                      tiers: f.tiers.includes(t)
                        ? f.tiers.filter((x) => x !== t)
                        : [...f.tiers, t],
                    }))
                  }
                  className={`rounded-full px-2.5 py-0.5 text-xs border transition-colors ${
                    filters.tiers.includes(t)
                      ? "bg-indigo-500/20 text-indigo-300 border-indigo-500/40"
                      : "bg-surface text-fg-muted border-border hover:border-border-strong"
                  }`}
                >
                  T{t} {tierCounts[t] != null ? `(${tierCounts[t]})` : ""}
                </button>
              ))}
            </div>
          </div>

          {/* Stage filter */}
          <div>
            <p className="text-xs uppercase tracking-wider text-fg-dim mb-2">
              Stage
            </p>
            <div className="flex flex-col gap-1">
              {QUEUE_STAGES.filter((s) => (stageCounts[s] ?? 0) > 0).map(
                (s) => (
                  <button
                    key={s}
                    type="button"
                    onClick={() =>
                      setFilters((f) => ({
                        ...f,
                        stages: f.stages.includes(s)
                          ? f.stages.filter((x) => x !== s)
                          : [...f.stages, s],
                      }))
                    }
                    className={`flex items-center justify-between rounded px-2 py-1 text-xs border transition-colors text-left ${
                      filters.stages.includes(s)
                        ? "bg-indigo-500/15 text-indigo-300 border-indigo-500/30"
                        : "bg-surface text-fg-muted border-border hover:border-border-strong"
                    }`}
                  >
                    <span>{stageLabel(s)}</span>
                    <span className="text-fg-dim">{stageCounts[s]}</span>
                  </button>
                )
              )}
            </div>
          </div>

          {/* Dartmouth toggle */}
          <ToggleSwitch
            on={filters.dartmouthOnly}
            onChange={(v) => setFilters((f) => ({ ...f, dartmouthOnly: v }))}
            label="Dartmouth only"
          />

          {/* Clear filters */}
          {activeFilterCount > 0 && (
            <button
              type="button"
              onClick={() =>
                setFilters({ tiers: [], stages: [], dartmouthOnly: false })
              }
              className="text-xs text-fg-dim hover:text-fg underline"
            >
              Clear filters ({activeFilterCount})
            </button>
          )}
        </div>

        <div className="px-4 py-3 border-t border-border">
          <button
            type="button"
            onClick={() => setShowShortcuts((v) => !v)}
            className="text-xs text-fg-dim hover:text-fg"
          >
            ? shortcuts
          </button>
        </div>
      </aside>

      {/* ── Center column (draft list) ─────────────────────────────────── */}
      <div className="w-[440px] shrink-0 border-r border-border overflow-y-auto">
        {visible.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-full text-center px-6">
            <p className="text-sm text-fg-muted">Queue is empty.</p>
            <p className="text-xs text-fg-dim mt-1">
              Drafts appear here after the next agent run.
            </p>
          </div>
        ) : (
          <ul>
            {visible.map((c, idx) => {
              const draft = draftsByContactId[c.id];
              const isFocused = idx === focusedIndex;
              return (
                <li
                  key={c.id}
                  onClick={() => setFocusedIndex(idx)}
                  className={`px-4 py-3 cursor-pointer border-b border-border transition-colors relative ${
                    isFocused
                      ? "bg-surface-2 border-l-4 border-l-indigo-500"
                      : "hover:bg-surface-2 border-l-4 border-l-transparent"
                  }`}
                  style={{ minHeight: "88px" }}
                >
                  {/* Line 1: badges */}
                  <div className="flex items-center gap-1.5 mb-1">
                    <Badge variant={tierBadgeVariant(c.tier)}>T{c.tier}</Badge>
                    <Badge variant={stageBadgeVariant(c.stage ?? "")}>
                      {stageLabel(c.stage ?? "")}
                    </Badge>
                  </div>
                  {/* Line 2: name + company */}
                  <p className="text-sm font-medium text-fg leading-tight">
                    {c.name}{" "}
                    <span className="text-fg-muted font-normal">
                      · {c.company}
                    </span>
                  </p>
                  {/* Location label — only rendered when state is set */}
                  {c.state && (
                    <p className="text-xs text-fg-dim mt-0.5">
                      {c.state} · {formatLocalTime(c.state, now) ?? ""}
                    </p>
                  )}
                  {/* Line 3: subject */}
                  <p className="text-xs text-fg truncate mt-0.5">
                    {draft?.subject ?? "—"}
                  </p>
                  {/* Line 4: body first line */}
                  <p className="text-xs text-fg-muted truncate mt-0.5">
                    {draft?.body?.split("\n")[0] ?? ""}
                  </p>
                  {/* Line 5: signal row */}
                  <div className="flex items-center gap-3 mt-1">
                    <span className="text-xs text-fg-dim">
                      {draft ? relativeTime(draft.drafted_at) : ""}
                    </span>
                    {draft && (
                      <span className="text-xs text-fg-dim">
                        Critic: {renderCritic(c, criticByContactId[c.id] ?? null)}
                      </span>
                    )}
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </div>

      {/* ── Right column (focused detail) ──────────────────────────────── */}
      <div className="flex-1 min-w-0 flex flex-col overflow-hidden">
        {!focused ? (
          <div className="flex flex-col items-center justify-center h-full text-center px-8">
            <p className="text-sm text-fg-muted">Queue is empty.</p>
            <p className="text-xs text-fg-dim mt-1">
              Drafts will appear here after the next agent run.
            </p>
          </div>
        ) : (
          <>
            {/* Scrollable content */}
            <div className="flex-1 overflow-y-auto px-6 py-5 space-y-5">
              {/* Block 1: Contact header */}
              <div>
                <h1 className="text-lg font-semibold text-fg leading-tight">
                  {focused.name}
                </h1>
                <p className="text-sm text-fg-muted">{focused.company}</p>
                {focused.role && (
                  <p className="text-xs text-fg-dim mt-0.5">{focused.role}</p>
                )}
                <div className="flex items-center gap-2 mt-2 flex-wrap">
                  <Badge variant={tierBadgeVariant(focused.tier)}>
                    T{focused.tier}
                  </Badge>
                  <Badge variant={stageBadgeVariant(focused.stage ?? "")}>
                    {stageLabel(focused.stage ?? "")}
                  </Badge>
                  {focusedDraft && (
                    <span className="text-xs text-fg-dim">
                      Drafted {relativeTime(focusedDraft.drafted_at)}
                    </span>
                  )}
                  {focused.dartmouth && (
                    <Badge variant="emerald">Dartmouth</Badge>
                  )}
                </div>
              </div>

              {/* Block 2: Signal block */}
              <div className="bg-surface border border-border rounded-lg p-4 space-y-2 text-sm">
                <div className="flex items-start justify-between gap-4">
                  <span className="text-xs text-fg-dim uppercase tracking-wider">
                    Critic
                  </span>
                  <span className="text-xs text-fg text-right">
                    {renderCritic(focused, focusedCritic)}
                  </span>
                </div>
                <div className="flex items-start justify-between gap-4">
                  <span className="text-xs text-fg-dim uppercase tracking-wider">
                    Pre-flight
                  </span>
                  <span className="text-xs text-fg-muted text-right">
                    {focusedDraft ? "✓ passed" : "—"}
                  </span>
                </div>
                <div className="flex items-start justify-between gap-4">
                  <span className="text-xs text-fg-dim uppercase tracking-wider">
                    Edited in Gmail
                  </span>
                  <span className="text-xs text-fg-dim text-right">—</span>
                </div>
                <div className="flex items-start justify-between gap-4">
                  <span className="text-xs text-fg-dim uppercase tracking-wider">
                    Resume
                  </span>
                  <span className="text-xs text-fg-muted text-right truncate max-w-[180px]">
                    {focused.resume_url ? (
                      <a
                        href={focused.resume_url}
                        target="_blank"
                        rel="noreferrer"
                        className="text-indigo-400 hover:underline"
                      >
                        View
                      </a>
                    ) : (
                      "(none)"
                    )}
                  </span>
                </div>
                <div className="flex items-start justify-between gap-4">
                  <span className="text-xs text-fg-dim uppercase tracking-wider">
                    Personalized
                  </span>
                  <span className="text-xs text-fg-muted text-right">
                    {focusedDraft?.body
                      ? personalizationSummary(focused, focusedDraft.body)
                      : "—"}
                  </span>
                </div>
              </div>

              {editMode === "quickfix" ? (
                <>
                  {/* Quick Fix: subject */}
                  <div>
                    <label className="block text-xs uppercase tracking-wider text-fg-muted mb-1.5">
                      Subject
                    </label>
                    <TextInput
                      value={editSubject}
                      onChange={(e) => setEditSubject(e.target.value)}
                    />
                  </div>
                  {/* Quick Fix: body */}
                  <div>
                    <label className="block text-xs uppercase tracking-wider text-fg-muted mb-1.5">
                      Body
                    </label>
                    <TextArea
                      value={editBody}
                      onChange={(e) => setEditBody(e.target.value)}
                      rows={14}
                    />
                  </div>
                </>
              ) : (
                <>
                  {/* Block 3: Subject */}
                  <div>
                    <p className="text-xs uppercase tracking-wider text-fg-dim mb-1">
                      Subject
                    </p>
                    <p className="text-base font-semibold text-fg">
                      {focusedDraft?.subject ?? "—"}
                    </p>
                  </div>

                  {/* Block 4: Body with highlights */}
                  <div>
                    <p className="text-xs uppercase tracking-wider text-fg-dim mb-1">
                      Body
                    </p>
                    <div className="bg-surface border border-border rounded-lg p-4 text-sm text-fg whitespace-pre-wrap leading-relaxed">
                      {focusedDraft?.body
                        ? highlight(focusedDraft.body, focused).map(
                            (seg, i) =>
                              seg.highlighted ? (
                                <mark
                                  key={i}
                                  className="bg-amber-500/15 text-fg rounded px-0.5"
                                  style={{ background: undefined }}
                                >
                                  {seg.text}
                                </mark>
                              ) : (
                                <span key={i}>{seg.text}</span>
                              )
                          )
                        : "—"}
                    </div>
                  </div>
                </>
              )}
            </div>

            {/* Block 5: Action bar (sticky bottom) */}
            <div className="shrink-0 border-t border-border px-6 py-4 flex items-center gap-3">
              {editMode === "quickfix" ? (
                <>
                  <button
                    type="button"
                    onClick={onSaveAndSend}
                    className="rounded-lg bg-indigo-600 hover:bg-indigo-500 text-white text-sm px-4 py-2 font-medium transition"
                  >
                    Save and Send
                  </button>
                  <button
                    type="button"
                    onClick={() => setEditMode("idle")}
                    className="rounded-lg border border-border text-sm px-4 py-2 text-fg-muted hover:text-fg hover:border-border-strong transition"
                  >
                    Cancel
                  </button>
                </>
              ) : (
                <>
                  {focusedDraft?.gmail_draft_id ? (
                    <button
                      type="button"
                      onClick={() => focused && onApprove(focused, focusedIndex)}
                      className="rounded-lg bg-indigo-600 hover:bg-indigo-500 text-white text-sm px-4 py-2 font-medium transition"
                    >
                      Approve and Send
                    </button>
                  ) : (
                    <Tooltip
                      content="No Gmail draft ID — re-run the agent to re-draft this contact"
                      side="top"
                    >
                      <button
                        type="button"
                        disabled
                        className="rounded-lg bg-indigo-600 text-white text-sm px-4 py-2 font-medium opacity-40 cursor-not-allowed"
                      >
                        Approve and Send
                      </button>
                    </Tooltip>
                  )}
                  <button
                    type="button"
                    onClick={openQuickFix}
                    className="rounded-lg border border-border text-sm px-4 py-2 text-fg-muted hover:text-fg hover:border-border-strong transition"
                  >
                    Quick Fix
                  </button>
                  {focusedDraft?.gmail_draft_id && (
                    <button
                      type="button"
                      onClick={() =>
                        window.open(
                          `https://mail.google.com/mail/u/0/#drafts?compose=${focusedDraft.gmail_draft_id}`,
                          "_blank"
                        )
                      }
                      className="rounded-lg border border-border text-sm px-4 py-2 text-fg-muted hover:text-fg hover:border-border-strong transition"
                      title="Opens as your first Google account (/u/0/). If that's not the right account, open Gmail first."
                    >
                      Edit in Gmail
                    </button>
                  )}
                  <button
                    type="button"
                    onClick={() =>
                      focused &&
                      setSkippedIds((prev) => new Set([...prev, focused.id]))
                    }
                    className="rounded-lg border border-border text-sm px-4 py-2 text-fg-muted hover:text-fg hover:border-border-strong transition"
                  >
                    Skip
                  </button>
                  <div className="ml-auto">
                    <button
                      type="button"
                      onClick={() =>
                        focused && onMarkDead(focused, focusedIndex)
                      }
                      className="rounded-lg border border-red-500/40 text-sm px-4 py-2 text-red-400 hover:bg-red-500/10 transition"
                    >
                      Mark dead
                    </button>
                  </div>
                </>
              )}
            </div>
          </>
        )}
      </div>

      {/* ── Keyboard shortcuts overlay ─────────────────────────────────── */}
      {showShortcuts && (
        <div
          className="fixed inset-0 bg-black/50 z-50 flex items-center justify-center"
          onClick={() => setShowShortcuts(false)}
        >
          <div
            className="bg-surface border border-border rounded-xl p-6 w-80 space-y-3"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between mb-2">
              <h3 className="text-sm font-semibold text-fg">
                Keyboard Shortcuts
              </h3>
              <button
                type="button"
                onClick={() => setShowShortcuts(false)}
                className="text-fg-dim hover:text-fg text-xs"
              >
                ✕
              </button>
            </div>
            {[
              ["j / ↓", "Next draft"],
              ["k / ↑", "Previous draft"],
              ["g g", "Jump to top"],
              ["G", "Jump to bottom"],
              ["e", "Approve and Send"],
              ["E", "Quick Fix"],
              ["o", "Edit in Gmail"],
              ["x", "Skip"],
              ["D", "Mark dead"],
              ["1 / 2 / 3", "Toggle tier filter"],
              ["Esc", "Close / clear filters"],
              ["?", "Toggle this panel"],
            ].map(([key, action]) => (
              <div key={key} className="flex items-center justify-between">
                <kbd className="rounded bg-surface-2 border border-border px-2 py-0.5 text-xs font-mono text-fg-muted">
                  {key}
                </kbd>
                <span className="text-xs text-fg-muted">{action}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

"use client";

import { useEffect, useRef, useState, useCallback } from "react";
import { toast } from "sonner";
import { supabase } from "@/lib/supabase";
import { ThreadView } from "@/components/ThreadView";
import { TextInput, TextArea } from "@/components/Field";
import { Badge } from "@/components/ui/Badge";
import type { Contact, DraftHistory, EmailMessage } from "@/lib/types";

// ── Types ──────────────────────────────────────────────────────────────────────

type PendingEntry = {
  timer: ReturnType<typeof setTimeout>;
  toastId: string | number;
  originalIndex: number;
  kind: "send" | "reply_status";
};

type ReplyStatusValue = "interested" | "call_scheduled" | "dead";

// ── Helpers ────────────────────────────────────────────────────────────────────

const CLASS_COLOR: Record<string, string> = {
  positive_reply: "text-emerald-400",
  soft_yes: "text-amber-400",
  bounced: "text-rose-400",
};

const CLASS_DOT: Record<string, string> = {
  positive_reply: "bg-emerald-400",
  soft_yes: "bg-amber-400",
  bounced: "bg-rose-400",
};

const CLASS_LABEL: Record<string, string> = {
  positive_reply: "POSITIVE",
  soft_yes: "SOFT YES",
  hard_no: "HARD NO",
  unrelated: "UNRELATED",
  auto_reply: "AUTO REPLY",
  out_of_office: "OUT OF OFFICE",
  bounced: "BOUNCED",
};

const SORT_PRIORITY: Record<string, number> = {
  positive_reply: 0,
  soft_yes: 1,
};

function classColor(status: string | null): string {
  return CLASS_COLOR[status ?? ""] ?? "text-fg-dim";
}

function classDot(status: string | null): string {
  return CLASS_DOT[status ?? ""] ?? "bg-fg-dim";
}

function classLabel(status: string | null): string {
  return CLASS_LABEL[status ?? ""] ?? (status ?? "—").replace(/_/g, " ").toUpperCase();
}

function relativeTime(ts: string): string {
  const diff = Date.now() - new Date(ts).getTime();
  const h = Math.floor(diff / 3_600_000);
  if (h < 1) return "just now";
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

function stripQuoted(body: string): string {
  return body
    .split("\n")
    .filter(
      (line) => !/^>/.test(line) && !/^On .+ wrote:\s*$/.test(line.trim())
    )
    .join("\n")
    .trim()
    .slice(0, 80);
}

function hasDraft(
  contact: Contact,
  draftsByContactId: Record<string, DraftHistory>
): boolean {
  const cs = contact.classifier_status ?? "";
  return (
    (cs === "positive_reply" || cs === "soft_yes") &&
    Boolean(draftsByContactId[contact.id])
  );
}

// ── Main component ─────────────────────────────────────────────────────────────

export function RepliesPage() {
  const [contacts, setContacts] = useState<Contact[]>([]);
  const [draftsByContactId, setDraftsByContactId] = useState<
    Record<string, DraftHistory>
  >({});
  const [lastIncomingByContactId, setLastIncomingByContactId] = useState<
    Record<string, EmailMessage>
  >({});
  const [focusedIndex, setFocusedIndex] = useState(0);
  const [editMode, setEditMode] = useState<"idle" | "quickfix">("idle");
  const [editSubject, setEditSubject] = useState("");
  const [editBody, setEditBody] = useState("");
  const [showShortcuts, setShowShortcuts] = useState(false);

  // navigating away cancels pending actions (deliberate trade-off)
  const pendingActions = useRef<Map<string, PendingEntry>>(new Map());
  const focusedContactId = useRef<string | null>(null);

  // ── Data fetch ───────────────────────────────────────────────────────────────

  const fetchData = useCallback(async () => {
    const { data: contactData } = await supabase
      .from("contacts")
      .select("*")
      .not("classifier_status", "is", null)
      .not("reply_status", "in", "(interested,call_scheduled,dead)")
      .is("deleted_at", null);

    const fetched = (contactData as Contact[]) ?? [];

    // Sort client-side: positive_reply first, soft_yes second, then created_at DESC
    fetched.sort((a, b) => {
      const pa = SORT_PRIORITY[a.classifier_status ?? ""] ?? 2;
      const pb = SORT_PRIORITY[b.classifier_status ?? ""] ?? 2;
      if (pa !== pb) return pa - pb;
      return (b.created_at ?? "") > (a.created_at ?? "") ? 1 : -1;
    });

    setContacts(fetched);

    if (fetched.length === 0) {
      setDraftsByContactId({});
      setLastIncomingByContactId({});
      return;
    }

    const ids = fetched.map((c) => c.id);

    const [{ data: draftData }, { data: msgData }] = await Promise.all([
      supabase
        .from("draft_history")
        .select("*")
        .in("contact_id", ids)
        .eq("stage", "reply_drafted")
        .is("sent_body", null)
        .order("drafted_at", { ascending: false }),
      supabase
        .from("email_messages")
        .select("*")
        .in("contact_id", ids)
        .eq("direction", "incoming")
        .order("sent_at", { ascending: false }),
    ]);

    // Keep latest reply draft per contact
    const drafts: Record<string, DraftHistory> = {};
    for (const d of (draftData as DraftHistory[]) ?? []) {
      if (!drafts[String(d.contact_id)]) {
        drafts[String(d.contact_id)] = d;
      }
    }
    setDraftsByContactId(drafts);

    // Keep latest incoming message per contact for left-list snippet
    const lastIncoming: Record<string, EmailMessage> = {};
    for (const m of (msgData as EmailMessage[]) ?? []) {
      const cid = String(m.contact_id);
      if (!lastIncoming[cid]) {
        lastIncoming[cid] = m;
      }
    }
    setLastIncomingByContactId(lastIncoming);
  }, []);

  useEffect(() => {
    fetchData();
    const id = setInterval(fetchData, 30_000);
    return () => clearInterval(id);
  }, [fetchData]);

  // Restore focus by contact_id after refresh
  useEffect(() => {
    if (contacts.length === 0) return;
    const targetId = focusedContactId.current;
    if (targetId) {
      const idx = visible.findIndex((c) => c.id === targetId);
      if (idx !== -1) {
        setFocusedIndex(idx);
        return;
      }
    }
    setFocusedIndex((prev) => Math.min(prev, visible.length - 1));
  }, [contacts.length]); // eslint-disable-line react-hooks/exhaustive-deps

  // Track focused contact_id
  const visible = contacts.filter((c) => !pendingActions.current.has(c.id));

  useEffect(() => {
    focusedContactId.current = visible[focusedIndex]?.id ?? null;
  }, [focusedIndex, visible]);

  const focused = visible[focusedIndex] ?? null;
  const focusedDraft = focused ? draftsByContactId[focused.id] ?? null : null;
  const focusedHasDraft = focused ? hasDraft(focused, draftsByContactId) : false;

  // ── Approve and send ─────────────────────────────────────────────────────────

  const onUndo = useCallback((contactId: string, originalIndex: number) => {
    const entry = pendingActions.current.get(contactId);
    if (!entry) return;
    clearTimeout(entry.timer);
    toast.dismiss(entry.toastId);
    pendingActions.current.delete(contactId);
    toast.info("Canceled");
    setContacts((prev) => [...prev]);
    setFocusedIndex(originalIndex);
  }, []);

  const onApprove = useCallback(
    (contact: Contact, idx: number) => {
      if (pendingActions.current.has(contact.id)) return;

      setFocusedIndex(idx < visible.length - 1 ? idx : Math.max(0, idx - 1));

      const toastId = toast(`Sending to ${contact.name ?? "contact"}…`, {
        duration: 5000,
        action: {
          label: "Undo",
          onClick: () => onUndo(contact.id, idx),
        },
      });

      const timer = setTimeout(async () => {
        pendingActions.current.delete(contact.id);
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

      pendingActions.current.set(contact.id, {
        timer,
        toastId,
        originalIndex: idx,
        kind: "send",
      });

      setContacts((prev) => [...prev]);
    },
    [visible.length, fetchData, onUndo] // eslint-disable-line react-hooks/exhaustive-deps
  );

  // ── Mark reply_status ────────────────────────────────────────────────────────

  const onMarkReplyStatus = useCallback(
    (contact: Contact, idx: number, value: ReplyStatusValue) => {
      if (pendingActions.current.has(contact.id)) return;

      setFocusedIndex(idx < visible.length - 1 ? idx : Math.max(0, idx - 1));

      const label =
        value === "interested"
          ? "interested"
          : value === "call_scheduled"
          ? "call scheduled"
          : "dead";

      const toastId = toast(
        `Marked ${contact.name ?? "contact"} as ${label} — Undo (5s)`,
        {
          duration: 5000,
          action: {
            label: "Undo",
            onClick: () => onUndo(contact.id, idx),
          },
        }
      );

      const timer = setTimeout(async () => {
        pendingActions.current.delete(contact.id);
        try {
          await supabase
            .from("contacts")
            .update({ reply_status: value })
            .eq("id", contact.id);
          fetchData();
        } catch {
          toast.error(`Failed to mark ${label}`);
          setContacts((prev) => {
            const next = [...prev];
            next.splice(idx, 0, contact);
            return next;
          });
        }
      }, 5000);

      pendingActions.current.set(contact.id, {
        timer,
        toastId,
        originalIndex: idx,
        kind: "reply_status",
      });

      setContacts((prev) => [...prev]);
    },
    [visible.length, fetchData, onUndo] // eslint-disable-line react-hooks/exhaustive-deps
  );

  // ── Re-classify (reset garbled / wrong classification) ──────────────────────

  const onReclassify = useCallback(
    async (contact: Contact, idx: number) => {
      if (pendingActions.current.has(contact.id)) return;
      try {
        await Promise.all([
          supabase
            .from("contacts")
            .update({ classifier_status: null })
            .eq("id", contact.id),
          supabase
            .from("email_messages")
            .delete()
            .eq("contact_id", contact.id)
            .eq("direction", "incoming"),
        ]);
        // Remove from list — classifier_status=null means it won't reappear until
        // the monitor re-classifies on its next run.
        setContacts((prev) => prev.filter((c) => c.id !== contact.id));
        setFocusedIndex((prev) => Math.min(prev, Math.max(0, idx - 1)));
        toast.success(
          `Re-classify queued for ${contact.name ?? "contact"} — updates on next monitor run`
        );
      } catch {
        toast.error("Failed to reset classifier status");
      }
    },
    [] // eslint-disable-line react-hooks/exhaustive-deps
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

  // ── Unmount: abort all pending actions ───────────────────────────────────────
  useEffect(() => {
    return () => {
      for (const entry of pendingActions.current.values()) {
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
      } else if (e.key === "e" && focused && focusedHasDraft) {
        onApprove(focused, focusedIndex);
      } else if (e.key === "E" && focusedHasDraft) {
        openQuickFix();
      } else if (e.key === "o") {
        if (focusedDraft?.gmail_draft_id) {
          window.open(
            `https://mail.google.com/mail/u/0/#drafts?compose=${focusedDraft.gmail_draft_id}`,
            "_blank"
          );
        } else {
          window.open("https://mail.google.com/mail/u/0/#inbox", "_blank");
        }
      } else if (e.key === "i" && focused) {
        onMarkReplyStatus(focused, focusedIndex, "interested");
      } else if (e.key === "c" && focused) {
        onMarkReplyStatus(focused, focusedIndex, "call_scheduled");
      } else if (e.key === "D" && focused) {
        onMarkReplyStatus(focused, focusedIndex, "dead");
      } else if (e.key === "?") {
        setShowShortcuts((v) => !v);
      } else if (e.key === "Escape") {
        if (editMode === "quickfix") setEditMode("idle");
        else if (showShortcuts) setShowShortcuts(false);
      }
    }

    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [
    visible,
    focused,
    focusedIndex,
    focusedDraft,
    focusedHasDraft,
    editMode,
    showShortcuts,
    onApprove,
    onMarkReplyStatus,
    openQuickFix,
  ]);

  // ── Render ───────────────────────────────────────────────────────────────────

  return (
    <div className="flex h-[calc(100vh-64px)] overflow-hidden">
      {/* ── Left column (triage list) ──────────────────────────────────── */}
      <aside className="w-[320px] shrink-0 border-r border-border flex flex-col overflow-hidden">
        <div className="px-4 py-4 border-b border-border shrink-0">
          <div className="flex items-center justify-between">
            <h2 className="text-xs font-semibold uppercase tracking-wider text-fg-muted">
              Needs Response
            </h2>
            <span className="rounded-full bg-indigo-500/20 border border-indigo-500/40 px-2 py-0.5 text-xs text-indigo-300">
              {visible.length}
            </span>
          </div>
        </div>

        <div className="flex-1 overflow-y-auto">
          {visible.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-full text-center px-6 py-12">
              <p className="text-sm text-fg-muted">No replies to triage.</p>
              <p className="text-xs text-fg-dim mt-1">
                Contacts appear here after a reply is detected.
              </p>
            </div>
          ) : (
            <ul>
              {visible.map((c, idx) => {
                const lastMsg = lastIncomingByContactId[c.id];
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
                    style={{ minHeight: "96px" }}
                  >
                    {/* Line 1: classifier dot + label */}
                    <div className="flex items-center gap-1.5 mb-1">
                      <span
                        className={`inline-block w-2 h-2 rounded-full shrink-0 ${classDot(c.classifier_status)}`}
                      />
                      <span
                        className={`text-xs font-medium ${classColor(c.classifier_status)}`}
                      >
                        {classLabel(c.classifier_status)}
                      </span>
                    </div>
                    {/* Line 2: name + company */}
                    <p className="text-sm font-medium text-fg leading-tight">
                      {c.name}{" "}
                      <span className="text-fg-muted font-normal">
                        · {c.company}
                      </span>
                    </p>
                    {/* Line 3: incoming snippet */}
                    {lastMsg?.body && (
                      <p className="text-xs text-fg-muted truncate mt-0.5 italic">
                        "{stripQuoted(lastMsg.body)}"
                      </p>
                    )}
                    {/* Line 4: timestamp */}
                    {lastMsg?.sent_at && (
                      <p className="text-xs text-fg-dim mt-0.5">
                        {relativeTime(lastMsg.sent_at)}
                      </p>
                    )}
                  </li>
                );
              })}
            </ul>
          )}
        </div>

        <div className="px-4 py-3 border-t border-border shrink-0">
          <button
            type="button"
            onClick={() => setShowShortcuts((v) => !v)}
            className="text-xs text-fg-dim hover:text-fg"
          >
            ? shortcuts
          </button>
        </div>
      </aside>

      {/* ── Right column (focused detail) ──────────────────────────────── */}
      <div className="flex-1 min-w-0 flex flex-col overflow-hidden">
        {!focused ? (
          <div className="flex flex-col items-center justify-center h-full text-center px-8">
            <p className="text-sm text-fg-muted">No replies to triage.</p>
            <p className="text-xs text-fg-dim mt-1">
              Contacts appear here after a reply is detected by the monitor.
            </p>
          </div>
        ) : (
          <>
            {/* Scrollable content */}
            <div className="flex-1 overflow-y-auto px-6 py-5 space-y-5">
              {/* Block 1: Contact header + classifier badge */}
              <div>
                <h1 className="text-lg font-semibold text-fg leading-tight">
                  {focused.name}
                </h1>
                <p className="text-sm text-fg-muted">{focused.company}</p>
                {focused.role && (
                  <p className="text-xs text-fg-dim mt-0.5">{focused.role}</p>
                )}
                <div className="flex items-center gap-2 mt-2 flex-wrap">
                  <span
                    className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xs font-medium ${
                      focused.classifier_status === "positive_reply"
                        ? "border-emerald-500/40 text-emerald-400 bg-emerald-500/10"
                        : focused.classifier_status === "soft_yes"
                        ? "border-amber-500/40 text-amber-400 bg-amber-500/10"
                        : "border-border text-fg-muted"
                    }`}
                  >
                    <span
                      className={`w-1.5 h-1.5 rounded-full ${classDot(focused.classifier_status)}`}
                    />
                    {classLabel(focused.classifier_status)}
                  </span>
                  {focused.tier != null && (
                    <Badge
                      variant={
                        focused.tier === 1
                          ? "indigo"
                          : focused.tier === 2
                          ? "emerald"
                          : "amber"
                      }
                    >
                      T{focused.tier}
                    </Badge>
                  )}
                  {focused.dartmouth && <Badge variant="emerald">Dartmouth</Badge>}
                </div>
              </div>

              {/* Block 2: Thread */}
              <div>
                <p className="text-xs uppercase tracking-wider text-fg-dim mb-2">
                  Thread
                </p>
                <ThreadView contactId={focused.id} />
              </div>

              {/* Block 3: Suggested reply or explanation */}
              {editMode === "quickfix" ? (
                <>
                  <div>
                    <label className="block text-xs uppercase tracking-wider text-fg-muted mb-1.5">
                      Subject
                    </label>
                    <TextInput
                      value={editSubject}
                      onChange={(e) => setEditSubject(e.target.value)}
                    />
                  </div>
                  <div>
                    <label className="block text-xs uppercase tracking-wider text-fg-muted mb-1.5">
                      Body
                    </label>
                    <TextArea
                      value={editBody}
                      onChange={(e) => setEditBody(e.target.value)}
                      rows={10}
                    />
                  </div>
                </>
              ) : focusedHasDraft && focusedDraft ? (
                <div>
                  <p className="text-xs uppercase tracking-wider text-fg-dim mb-2">
                    Suggested Reply
                  </p>
                  <div className="bg-surface border border-border rounded-lg p-4 space-y-3">
                    <div>
                      <p className="text-xs text-fg-dim mb-0.5">Subject</p>
                      <p className="text-sm font-medium text-fg">
                        {focusedDraft.subject ?? "—"}
                      </p>
                    </div>
                    <div>
                      <p className="text-xs text-fg-dim mb-0.5">Body</p>
                      <p className="text-sm text-fg whitespace-pre-wrap leading-relaxed">
                        {focusedDraft.body ?? "—"}
                      </p>
                    </div>
                  </div>
                </div>
              ) : (
                <div className="bg-surface border border-border rounded-lg p-4">
                  <p className="text-sm text-fg-muted">
                    No suggested reply drafted.
                  </p>
                  <p className="text-xs text-fg-dim mt-1">
                    {focused.classifier_status === "bounced"
                      ? "Email bounced — this address appears to be invalid. Consider updating the contact or marking dead."
                      : focused.classifier_status === "positive_reply" ||
                        focused.classifier_status === "soft_yes"
                      ? "The agent hasn't drafted a reply yet for this contact."
                      : "The agent only drafts replies for positive and soft-yes responses."}
                  </p>
                </div>
              )}
            </div>

            {/* Block 4: Action bar (sticky bottom) */}
            <div className="shrink-0 border-t border-border px-6 py-4">
              {editMode === "quickfix" ? (
                <div className="flex items-center gap-3">
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
                </div>
              ) : (
                <>
                  <div className="flex items-center gap-3 flex-wrap">
                    {focusedHasDraft ? (
                      <>
                        <button
                          type="button"
                          onClick={() => focused && onApprove(focused, focusedIndex)}
                          className="rounded-lg bg-indigo-600 hover:bg-indigo-500 text-white text-sm px-4 py-2 font-medium transition"
                        >
                          Approve and Send
                        </button>
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
                            title="Opens as your first Google account (/u/0/)."
                          >
                            Edit in Gmail
                          </button>
                        )}
                      </>
                    ) : (
                      <>
                        <button
                          type="button"
                          onClick={() =>
                            window.open(
                              "https://mail.google.com/mail/u/0/#inbox",
                              "_blank"
                            )
                          }
                          className="rounded-lg border border-border text-sm px-4 py-2 text-fg-muted hover:text-fg hover:border-border-strong transition"
                        >
                          Open in Gmail
                        </button>
                        {focused.classifier_status === "unrelated" && (
                          <button
                            type="button"
                            onClick={() => focused && onReclassify(focused, focusedIndex)}
                            className="rounded-lg border border-border text-sm px-4 py-2 text-fg-muted hover:text-fg hover:border-border-strong transition"
                            title="Clears this classification so the monitor re-detects and re-classifies on its next run."
                          >
                            Re-classify
                          </button>
                        )}
                      </>
                    )}
                  </div>
                  <div className="flex items-center gap-2 mt-3 pt-3 border-t border-border">
                    <button
                      type="button"
                      onClick={() =>
                        focused && onMarkReplyStatus(focused, focusedIndex, "interested")
                      }
                      className="rounded-lg border border-border text-xs px-3 py-1.5 text-fg-muted hover:text-fg hover:border-border-strong transition"
                    >
                      Mark interested
                    </button>
                    <button
                      type="button"
                      onClick={() =>
                        focused &&
                        onMarkReplyStatus(focused, focusedIndex, "call_scheduled")
                      }
                      className="rounded-lg border border-border text-xs px-3 py-1.5 text-fg-muted hover:text-fg hover:border-border-strong transition"
                    >
                      Mark call scheduled
                    </button>
                    <button
                      type="button"
                      onClick={() =>
                        focused && onMarkReplyStatus(focused, focusedIndex, "dead")
                      }
                      className="rounded-lg border border-red-500/40 text-xs px-3 py-1.5 text-red-400 hover:bg-red-500/10 transition"
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
              ["j / ↓", "Next reply"],
              ["k / ↑", "Previous reply"],
              ["e", "Approve and Send (if draft)"],
              ["E", "Quick Fix (if draft)"],
              ["o", "Edit in Gmail / Open Gmail"],
              ["i", "Mark interested"],
              ["c", "Mark call scheduled"],
              ["D", "Mark dead"],
              ["Esc", "Close Quick Fix"],
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

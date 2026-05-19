"use client";

import { useEffect, useRef, useState, useCallback } from "react";
import { Copy, ExternalLink, UserPlus, SearchX } from "lucide-react";
import { supabase } from "@/lib/supabase";
import {
  type Contact,
  type ReplyStatus,
  type ContactsQueryFilters,
  OUTREACH_STAGES,
  APPLIED_STAGES,
  REPLY_STATUSES,
  EMPTY_FILTERS,
  filtersEqual,
} from "@/lib/types";
import { ContactsFilters } from "@/components/ContactsFilters";
import { Badge } from "@/components/ui/Badge";
import { Skeleton } from "@/components/ui/Skeleton";
import { EmptyState } from "@/components/ui/EmptyState";
import { Tooltip } from "@/components/ui/Tooltip";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetDescription,
  SheetBody,
  SheetClose,
} from "@/components/ui/Sheet";
import {
  Select,
  SelectTrigger,
  SelectValue,
  SelectContent,
  SelectItem,
  SelectGroup,
  SelectLabel,
  SelectSeparator,
} from "@/components/ui/Select";
import { ConfirmModal } from "@/components/ui/ConfirmModal";
import { TextArea, TierSelector } from "@/components/Field";
import { ThreadView } from "@/components/ThreadView";

// ── Constants ──────────────────────────────────────────────────────────────────

const PAGE_SIZE = 30;
const GRID_COLS = "grid-cols-[1fr_140px_60px_90px_100px_80px]";

// ── Stage helpers ──────────────────────────────────────────────────────────────

const STAGE_LABELS: Record<string, string> = {
  new: "New",
  first_touch_drafted: "First Touch Draft",
  first_touch_sent: "First Touch Sent",
  followup1_drafted: "Followup 1 Draft",
  followup1_sent: "Followup 1 Sent",
  followup2_drafted: "Followup 2 Draft",
  followup2_sent: "Followup 2 Sent",
  breakup_drafted: "Breakup Draft",
  breakup_sent: "Breakup Sent",
  applied_intro_drafted: "App Intro Draft",
  applied_intro_sent: "App Intro Sent",
  applied_followup_drafted: "App Followup Draft",
  applied_followup_sent: "App Followup Sent",
  closed: "Closed",
  bounced: "Bounced",
  unsubscribed: "Unsub",
  positive_reply: "Replied",
  research: "Research",
  ready: "Ready",
  engaged: "Engaged",
};

function stageLabel(stage: string): string {
  return STAGE_LABELS[stage] ?? stage;
}

type BadgeVariant = "default" | "indigo" | "emerald" | "amber" | "red" | "muted";

function stageVariant(stage: string): BadgeVariant {
  if (stage.endsWith("_drafted")) return "amber";
  if (stage.endsWith("_sent")) return "indigo";
  if (
    stage.endsWith("_replied") ||
    stage === "positive_reply" ||
    stage === "engaged"
  )
    return "emerald";
  if (
    stage === "closed" ||
    stage === "bounced" ||
    stage === "unsubscribed"
  )
    return "muted";
  return "default";
}

function tierVariant(tier: number | null): BadgeVariant {
  return tier === 1 ? "indigo" : tier === 2 ? "default" : "muted";
}

function tierTooltip(tier: number | null): string {
  return tier === 1
    ? "Tier 1 — top priority"
    : tier === 2
    ? "Tier 2 — strong target"
    : "Tier 3 — backup";
}

function replyVariant(status: ReplyStatus | null): BadgeVariant {
  if (!status || status === "no_reply") return "muted";
  if (status === "interested" || status === "call_scheduled") return "emerald";
  if (status === "dead") return "red";
  return "default";
}

function formatRelativeDate(dateStr: string | null | undefined): string {
  if (!dateStr) return "Never";
  const date = new Date(dateStr);
  if (isNaN(date.getTime())) return "Never";
  const diffMs = Date.now() - date.getTime();
  const diffSec = Math.floor(diffMs / 1000);
  if (diffSec < 60) return "Just now";
  const diffMin = Math.floor(diffSec / 60);
  if (diffMin < 60) return `${diffMin}m ago`;
  const diffHrs = Math.floor(diffMin / 60);
  if (diffHrs < 24) return `${diffHrs}h ago`;
  const diffDays = Math.floor(diffHrs / 24);
  if (diffDays < 7) return `${diffDays}d ago`;
  return `${Math.floor(diffDays / 7)}w ago`;
}

function extractTuckYear(notes: string | null | undefined): string | null {
  if (!notes) return null;
  const m = /Tuck Class of (\d{4})/.exec(notes);
  return m ? m[1].slice(-2) : null;
}

// ── Query builder ──────────────────────────────────────────────────────────────

function buildContactsQuery(filters: ContactsQueryFilters, cursor: string | null) {
  const escaped = filters.nameOrCompany.trim().replace(/[%_]/g, "\\$&");

  // Build filters first, then apply ordering and limit last.
  // This keeps the chain valid: .order().limit() always resolves the query.
  let q = supabase
    .from("contacts")
    .select("*")
    .is("deleted_at", null);

  if (escaped) {
    q = q.or(`name.ilike.%${escaped}%,company.ilike.%${escaped}%`);
  }
  if (filters.stages.length > 0) q = q.in("stage", filters.stages);
  if (filters.tiers.length > 0) q = q.in("tier", filters.tiers);
  if (filters.modes.length > 0) q = q.in("mode", filters.modes);
  if (filters.dartmouthOnly) q = q.eq("dartmouth", true);
  if (filters.needsResponseOnly) {
    q = q
      .in("classifier_status", ["positive_reply", "soft_yes"])
      .not("reply_status", "in", "(interested,call_scheduled,dead)");
  }
  if (cursor) q = q.lt("created_at", cursor);

  // .limit() must be last — resolves the query chain
  return q.order("created_at", { ascending: false }).limit(PAGE_SIZE);
}

// ── Component ──────────────────────────────────────────────────────────────────

type Props = {
  refreshKey: number;
  onError: (msg: string) => void;
  onSuccess: (msg: string) => void;
};

export function ContactsList({ refreshKey, onError, onSuccess }: Props) {
  const [contacts, setContacts] = useState<Contact[]>([]);
  const [filters, setFilters] = useState<ContactsQueryFilters>(EMPTY_FILTERS);
  const [lastFetchedCreatedAt, setLastFetchedCreatedAt] = useState<string | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [selectedContact, setSelectedContact] = useState<Contact | null>(null);
  const [showDeleteModal, setShowDeleteModal] = useState(false);
  const [deleting, setDeleting] = useState(false);

  // Sheet local state
  const [notes, setNotes] = useState("");

  const fetchIdRef = useRef(0);
  const prevFiltersRef = useRef<ContactsQueryFilters>(EMPTY_FILTERS);
  const sentinelRef = useRef<HTMLDivElement | null>(null);
  const listContainerRef = useRef<HTMLDivElement | null>(null);

  // Sync sheet notes when selected contact changes
  useEffect(() => {
    setNotes(selectedContact?.notes ?? "");
  }, [selectedContact?.id]);

  // ── Fetch functions ──────────────────────────────────────────────────────────

  const fetchInitial = useCallback(
    async (currentFilters: ContactsQueryFilters) => {
      const fetchId = ++fetchIdRef.current;
      setLoading(true);
      const { data, error } = await buildContactsQuery(currentFilters, null);
      if (fetchId !== fetchIdRef.current) return;
      if (error) {
        onError(`Failed to load contacts: ${error.message}`);
        setLoading(false);
        return;
      }
      const rows = (data ?? []) as Contact[];
      setContacts(rows);
      setLastFetchedCreatedAt(rows.length ? (rows[rows.length - 1].created_at ?? null) : null);
      setHasMore(rows.length === PAGE_SIZE);
      setLoading(false);
      if (listContainerRef.current) listContainerRef.current.scrollTop = 0;
    },
    [onError]
  );

  const fetchMore = useCallback(async () => {
    if (loadingMore || !hasMore || !lastFetchedCreatedAt) return;
    const fetchId = ++fetchIdRef.current;
    setLoadingMore(true);
    const { data, error } = await buildContactsQuery(filters, lastFetchedCreatedAt);
    if (fetchId !== fetchIdRef.current) {
      setLoadingMore(false);
      return;
    }
    if (error) {
      setLoadingMore(false);
      onError("Failed to load more contacts");
      return;
    }
    const rows = (data ?? []) as Contact[];
    if (rows.length > 0) {
      setContacts((prev) => [...prev, ...rows]);
      setLastFetchedCreatedAt(rows[rows.length - 1].created_at ?? null);
      setHasMore(rows.length === PAGE_SIZE);
    } else {
      setHasMore(false);
    }
    setLoadingMore(false);
  }, [loadingMore, hasMore, lastFetchedCreatedAt, filters, onError]);

  // ── Filter changes (debounce text only) ─────────────────────────────────────

  useEffect(() => {
    const prev = prevFiltersRef.current;
    if (filtersEqual(prev, filters)) return;

    const onlyTextChanged =
      prev.nameOrCompany !== filters.nameOrCompany &&
      prev.stages === filters.stages &&
      prev.tiers === filters.tiers &&
      prev.modes === filters.modes &&
      prev.dartmouthOnly === filters.dartmouthOnly;

    if (onlyTextChanged) {
      const t = setTimeout(() => {
        fetchInitial(filters);
        prevFiltersRef.current = filters;
      }, 300);
      return () => clearTimeout(t);
    } else {
      fetchInitial(filters);
      prevFiltersRef.current = filters;
    }
  }, [filters, fetchInitial]);

  // ── Infinite scroll ──────────────────────────────────────────────────────────

  useEffect(() => {
    const node = sentinelRef.current;
    if (!node) return;
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries[0].isIntersecting) fetchMore();
      },
      { rootMargin: "200px" }
    );
    observer.observe(node);
    return () => observer.disconnect();
  }, [fetchMore]);

  // ── refreshKey ───────────────────────────────────────────────────────────────

  useEffect(() => {
    fetchInitial(filters);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refreshKey]);

  // ── Optimistic stage update ──────────────────────────────────────────────────

  async function handleStageChange(stage: string) {
    if (!selectedContact) return;
    const prev = selectedContact;
    const updated = { ...selectedContact, stage };
    setSelectedContact(updated);
    setContacts((cs) => cs.map((c) => (c.id === updated.id ? updated : c)));

    const { error } = await supabase
      .from("contacts")
      .update({ stage })
      .eq("id", selectedContact.id);

    if (error) {
      setSelectedContact(prev);
      setContacts((cs) => cs.map((c) => (c.id === prev.id ? prev : c)));
      onError(`Failed to update stage: ${error.message}`);
    }
  }

  // ── Optimistic tier update ───────────────────────────────────────────────────

  async function handleTierChange(tier: number) {
    if (!selectedContact) return;
    const prev = selectedContact;
    const updated = { ...selectedContact, tier };
    setSelectedContact(updated);
    setContacts((cs) => cs.map((c) => (c.id === updated.id ? updated : c)));

    const { error } = await supabase
      .from("contacts")
      .update({ tier })
      .eq("id", selectedContact.id);

    if (error) {
      setSelectedContact(prev);
      setContacts((cs) => cs.map((c) => (c.id === prev.id ? prev : c)));
      onError(`Failed to update tier: ${error.message}`);
    }
  }

  // ── Notes autosave on blur ───────────────────────────────────────────────────

  async function handleNotesBlur() {
    if (!selectedContact || notes === (selectedContact.notes ?? "")) return;
    const { error } = await supabase
      .from("contacts")
      .update({ notes })
      .eq("id", selectedContact.id);
    if (error) {
      onError(`Failed to save notes: ${error.message}`);
      setNotes(selectedContact.notes ?? "");
      return;
    }
    const updated = { ...selectedContact, notes };
    setSelectedContact(updated);
    setContacts((cs) => cs.map((c) => (c.id === updated.id ? updated : c)));
    onSuccess("Notes saved");
  }

  // ── Soft delete ──────────────────────────────────────────────────────────────

  async function handleDelete() {
    if (!selectedContact) return;
    setDeleting(true);
    const { error } = await supabase
      .from("contacts")
      .update({ deleted_at: new Date().toISOString() })
      .eq("id", selectedContact.id);
    setDeleting(false);
    if (error) {
      onError(`Failed to delete: ${error.message}`);
      return;
    }
    setContacts((prev) => prev.filter((c) => c.id !== selectedContact.id));
    setSelectedContact(null);
    setShowDeleteModal(false);
    onSuccess("Contact deleted");
  }

  // ── Render ───────────────────────────────────────────────────────────────────

  const stages =
    selectedContact?.mode === "applied" ? APPLIED_STAGES : OUTREACH_STAGES;

  return (
    <>
      <ContactsFilters filters={filters} onChange={setFilters} />

      <div ref={listContainerRef}>
        {/* Sticky column header */}
        <div
          className={`sticky top-0 z-10 bg-bg/95 backdrop-blur-sm border-b border-border px-4 py-2 grid ${GRID_COLS} gap-3 text-xs uppercase tracking-wider text-fg-dim`}
        >
          <div>Name / Company</div>
          <div>Stage</div>
          <div>Tier</div>
          <div>Mode</div>
          <div>Last contact</div>
          <div>Reply</div>
        </div>

        {/* Initial loading skeletons */}
        {loading && contacts.length === 0 && (
          <>
            {Array.from({ length: 8 }).map((_, i) => (
              <div
                key={i}
                className={`grid ${GRID_COLS} gap-3 px-4 py-2.5 border-b border-border/50`}
              >
                <div>
                  <Skeleton className="h-4 w-32 mb-1.5" />
                  <Skeleton className="h-3 w-24" />
                </div>
                <Skeleton className="h-5 w-20 self-center" />
                <Skeleton className="h-5 w-10 self-center" />
                <Skeleton className="h-4 w-16 self-center" />
                <Skeleton className="h-4 w-12 self-center" />
                <Skeleton className="h-5 w-14 self-center" />
              </div>
            ))}
          </>
        )}

        {/* Empty states */}
        {!loading && contacts.length === 0 && filtersEqual(filters, EMPTY_FILTERS) && (
          <EmptyState
            icon={<UserPlus className="size-5 text-fg-muted" />}
            title="No contacts yet"
            description="Add your first contact using the form above."
          />
        )}
        {!loading && contacts.length === 0 && !filtersEqual(filters, EMPTY_FILTERS) && (
          <EmptyState
            icon={<SearchX className="size-5 text-fg-muted" />}
            title="No contacts match these filters"
            description="Try removing a filter or clearing all of them."
            action={
              <button
                type="button"
                onClick={() => setFilters(EMPTY_FILTERS)}
                className="text-indigo-300 hover:text-indigo-200 text-sm transition-colors"
              >
                Clear filters
              </button>
            }
          />
        )}

        {/* Contact rows */}
        {contacts.map((c) => {
          const tuckYear = c.dartmouth ? extractTuckYear(c.notes) : null;
          return (
            <button
              key={c.id}
              type="button"
              onClick={() => setSelectedContact(c)}
              className={`w-full grid ${GRID_COLS} gap-3 px-4 py-2.5 border-b border-border/50 hover:bg-surface-2 transition-colors text-left text-sm cursor-pointer`}
            >
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <span className="text-fg font-medium truncate">
                    {c.name ?? "—"}
                  </span>
                  {tuckYear && (
                    <Badge variant="emerald" className="text-[10px] shrink-0">
                      T&apos;{tuckYear}
                    </Badge>
                  )}
                </div>
                <div className="text-fg-muted text-xs truncate">
                  {c.company ?? "—"}
                </div>
              </div>
              <div className="flex items-center">
                <Tooltip content={c.stage ?? ""}>
                  <span>
                    <Badge variant={stageVariant(c.stage ?? "")}>
                      {stageLabel(c.stage ?? "")}
                    </Badge>
                  </span>
                </Tooltip>
              </div>
              <div className="flex items-center">
                <Tooltip content={tierTooltip(c.tier)}>
                  <span>
                    <Badge variant={tierVariant(c.tier)}>
                      T{c.tier ?? "?"}
                    </Badge>
                  </span>
                </Tooltip>
              </div>
              <div className="flex items-center text-fg-muted text-xs capitalize">
                {c.mode ?? "—"}
              </div>
              <div className="flex items-center">
                <Tooltip content={c.last_emailed ?? "Never emailed"}>
                  <span className="text-fg-muted text-xs">
                    {formatRelativeDate(c.last_emailed)}
                  </span>
                </Tooltip>
              </div>
              <div className="flex items-center">
                {c.reply_status && c.reply_status !== "no_reply" && (
                  <Badge variant={replyVariant(c.reply_status)}>
                    {c.reply_status.replace(/_/g, " ")}
                  </Badge>
                )}
              </div>
            </button>
          );
        })}

        {/* Sentinel for infinite scroll */}
        <div ref={sentinelRef} aria-hidden="true" className="h-px" />

        {/* Load-more skeletons */}
        {loadingMore && (
          <>
            {Array.from({ length: 3 }).map((_, i) => (
              <div
                key={i}
                className={`grid ${GRID_COLS} gap-3 px-4 py-2.5 border-b border-border/50`}
              >
                <div>
                  <Skeleton className="h-4 w-32 mb-1.5" />
                  <Skeleton className="h-3 w-24" />
                </div>
                <Skeleton className="h-5 w-20 self-center" />
                <Skeleton className="h-5 w-10 self-center" />
                <Skeleton className="h-4 w-16 self-center" />
                <Skeleton className="h-4 w-12 self-center" />
                <Skeleton className="h-5 w-14 self-center" />
              </div>
            ))}
          </>
        )}

        {/* End of list */}
        {!hasMore && contacts.length > 0 && (
          <div className="text-center py-6 text-fg-dim text-xs">
            All contacts loaded
          </div>
        )}
      </div>

      {/* ── Side sheet ──────────────────────────────────────────────────────── */}
      <Sheet
        open={selectedContact !== null}
        onOpenChange={(open) => {
          if (!open) setSelectedContact(null);
        }}
      >
        <SheetContent>
          <SheetHeader>
            <SheetTitle>{selectedContact?.name ?? "Contact"}</SheetTitle>
            <SheetDescription>
              <span className="inline-flex items-center gap-2">
                {selectedContact?.company}
                {selectedContact && (
                  <Badge variant={tierVariant(selectedContact.tier)}>
                    T{selectedContact.tier ?? "?"}
                  </Badge>
                )}
              </span>
            </SheetDescription>
            <SheetClose />
          </SheetHeader>

          <SheetBody>
            {selectedContact && (
              <>
                {/* Status section */}
                <div className="space-y-4">
                  <div>
                    <label className="text-xs uppercase tracking-wider text-fg-dim mb-1.5 block">
                      Stage
                    </label>
                    <Select
                      value={selectedContact.stage ?? "new"}
                      onValueChange={handleStageChange}
                    >
                      <SelectTrigger className="w-full">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {selectedContact.mode !== "applied" && (
                          <SelectGroup>
                            <SelectLabel>Outreach</SelectLabel>
                            {OUTREACH_STAGES.filter(
                              (s) => s !== "closed"
                            ).map((s) => (
                              <SelectItem key={s} value={s}>
                                {stageLabel(s)}
                              </SelectItem>
                            ))}
                          </SelectGroup>
                        )}
                        {selectedContact.mode !== "outreach" && (
                          <SelectGroup>
                            <SelectLabel>Applied</SelectLabel>
                            {APPLIED_STAGES.filter(
                              (s) =>
                                s !== "closed" &&
                                (selectedContact.mode === "applied" || s !== "new")
                            ).map((s) => (
                              <SelectItem key={s} value={s}>
                                {stageLabel(s)}
                              </SelectItem>
                            ))}
                          </SelectGroup>
                        )}
                        <SelectSeparator />
                        <SelectItem value="closed">Closed</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>

                  <div>
                    <label className="text-xs uppercase tracking-wider text-fg-dim mb-1.5 block">
                      Reply status
                    </label>
                    <select
                      value={selectedContact.reply_status ?? "no_reply"}
                      onChange={(e) => {
                        const updated = {
                          ...selectedContact,
                          reply_status: e.target.value as ReplyStatus,
                        };
                        setSelectedContact(updated);
                        setContacts((cs) =>
                          cs.map((c) => (c.id === updated.id ? updated : c))
                        );
                        supabase
                          .from("contacts")
                          .update({ reply_status: e.target.value })
                          .eq("id", selectedContact.id)
                          .then(({ error }) => {
                            if (error)
                              onError(
                                `Failed to update reply status: ${error.message}`
                              );
                          });
                      }}
                      className="w-full rounded-md border border-border bg-surface-2 px-3 py-2 text-sm text-fg focus:outline-none focus:ring-2 focus:ring-indigo-500/40"
                    >
                      {REPLY_STATUSES.map((s) => (
                        <option key={s} value={s}>
                          {s.replace(/_/g, " ")}
                        </option>
                      ))}
                    </select>
                  </div>

                  {selectedContact.classifier_status && (
                    <div>
                      <label className="text-xs uppercase tracking-wider text-fg-dim mb-1.5 block">
                        Auto-classified
                      </label>
                      <p className="text-sm text-fg-muted">
                        {selectedContact.classifier_status.replace(/_/g, " ")}
                      </p>
                    </div>
                  )}

                  <div>
                    <label className="text-xs uppercase tracking-wider text-fg-dim mb-1.5 block">
                      Tier
                    </label>
                    <TierSelector
                      value={selectedContact.tier ?? 2}
                      onChange={handleTierChange}
                    />
                  </div>
                </div>

                <div className="h-px bg-border my-4" />

                {/* Contact details section */}
                <div className="space-y-3">
                  {selectedContact.email && (
                    <div>
                      <label className="text-xs uppercase tracking-wider text-fg-dim mb-1 block">
                        Email
                      </label>
                      <div className="flex items-center gap-2 text-sm text-fg-muted">
                        <span className="truncate">{selectedContact.email}</span>
                        <button
                          type="button"
                          onClick={() => {
                            navigator.clipboard.writeText(
                              selectedContact.email ?? ""
                            );
                            onSuccess("Email copied");
                          }}
                          className="text-fg-dim hover:text-fg p-1 rounded hover:bg-surface-2 shrink-0 transition-colors"
                          aria-label="Copy email"
                        >
                          <Copy className="size-3.5" />
                        </button>
                      </div>
                    </div>
                  )}

                  {selectedContact.role && (
                    <div>
                      <label className="text-xs uppercase tracking-wider text-fg-dim mb-1 block">
                        Role
                      </label>
                      <p className="text-sm text-fg-muted">{selectedContact.role}</p>
                    </div>
                  )}

                  {selectedContact.mode === "applied" && selectedContact.job_title && (
                    <div>
                      <label className="text-xs uppercase tracking-wider text-fg-dim mb-1 block">
                        Job Title
                      </label>
                      <p className="text-sm text-fg-muted">
                        {selectedContact.job_title}
                      </p>
                    </div>
                  )}

                  <div>
                    <label className="text-xs uppercase tracking-wider text-fg-dim mb-1 block">
                      Notes
                    </label>
                    <TextArea
                      value={notes}
                      onChange={(e) => setNotes(e.target.value)}
                      onBlur={handleNotesBlur}
                      rows={4}
                    />
                  </div>

                  <div className="text-xs text-fg-dim">
                    Last contacted:{" "}
                    {formatRelativeDate(selectedContact.last_emailed)}
                  </div>
                </div>

                {/* Agent context section */}
                {(selectedContact.message_id || selectedContact.followup_date) && (
                  <>
                    <div className="h-px bg-border my-4" />
                    <div className="space-y-2">
                      <div className="text-xs uppercase tracking-wider text-fg-dim">
                        Managed by agent
                      </div>
                      {selectedContact.message_id && (
                        <div className="text-xs text-fg-muted font-mono truncate">
                          msg: {selectedContact.message_id}
                        </div>
                      )}
                      {selectedContact.followup_date && (
                        <div className="text-xs text-fg-muted">
                          followup: {selectedContact.followup_date}
                        </div>
                      )}
                    </div>
                  </>
                )}

                {/* Email thread */}
                <div className="h-px bg-border my-4" />
                <div>
                  <div className="text-xs uppercase tracking-wider text-fg-dim mb-3">
                    Email thread
                  </div>
                  <ThreadView contactId={selectedContact.id} />
                </div>

                <div className="h-px bg-border my-4" />

                {/* Danger zone */}
                <div>
                  <div className="text-xs uppercase tracking-wider text-fg-dim mb-2">
                    Danger
                  </div>
                  <button
                    type="button"
                    onClick={() => setShowDeleteModal(true)}
                    className="bg-transparent text-red-300 border border-red-500/40 rounded-md px-3 py-2 text-sm hover:bg-red-500/10 transition-colors"
                  >
                    Delete contact
                  </button>
                </div>
              </>
            )}
          </SheetBody>
        </SheetContent>
      </Sheet>

      {/* ── Delete confirmation modal ────────────────────────────────────────── */}
      <ConfirmModal
        open={showDeleteModal}
        title="Delete this contact?"
        body={
          <div className="space-y-3">
            <p>
              This removes {selectedContact?.name} from your active contacts.
              You can recover them later via the Supabase dashboard. Drafts and
              sent emails in Gmail are not affected.
            </p>
            {selectedContact?.stage?.includes("_drafted") &&
              selectedContact?.message_id && (
                <div className="bg-amber-500/10 border border-amber-500/30 rounded-md p-3 text-amber-200 text-xs">
                  This contact has an active draft in Gmail Drafts. Deleting
                  here will NOT remove the draft from Gmail. Delete it manually
                  from Gmail Drafts first.
                </div>
              )}
          </div>
        }
        confirmLabel="Delete"
        confirmVariant="danger"
        loading={deleting}
        onCancel={() => setShowDeleteModal(false)}
        onConfirm={handleDelete}
      />
    </>
  );
}

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
  NETWORKING_STAGES,
  REPLY_STAGES,
  REPLY_STATUSES,
  EMPTY_FILTERS,
  filtersEqual,
} from "@/lib/types";
import { US_STATES } from "@/lib/timezone";
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
import { TextInput, TextArea, TierSelector, ToggleSwitch } from "@/components/Field";
import { ThreadView } from "@/components/ThreadView";

// ── Constants ──────────────────────────────────────────────────────────────────

const PAGE_SIZE = 30;
const GRID_COLS = "grid-cols-[1fr_140px_60px_90px_100px_80px_90px]";

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
  networking_drafted: "Networking Draft",
  networking_sent: "Networking Sent",
  networking_followup_drafted: "Networking Followup Draft",
  networking_followup_sent: "Networking Followup Sent",
  closed: "Closed",
  bounced: "Bounced",
  unsubscribed: "Unsub",
  positive_reply: "Replied",
  research: "Research",
  ready: "Ready",
  engaged: "Engaged",
  reply_drafted: "Reply Draft",
  reply_sent: "Reply Sent",
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

// H-1B sponsorship signal — decision support, never a verdict. "unknown" and
// missing company_intel both render as "No data", not "Does not sponsor".
function signalVariant(intel: Contact["company_intel"]): BadgeVariant {
  const status = intel?.match_status;
  if (status === "auto" || status === "confirmed") return "emerald";
  if (status === "needs_review") return "amber";
  return "muted";
}

function signalLabel(intel: Contact["company_intel"]): string {
  const status = intel?.match_status;
  if (status === "auto" || status === "confirmed") return "Sponsor";
  if (status === "needs_review") return "Review";
  if (status === "rejected") return "No match";
  return "No data";
}

function signalTooltip(intel: Contact["company_intel"]): string {
  const status = intel?.match_status;
  if (status === "auto" || status === "confirmed")
    return "Sponsors H-1B (recent filings) — decision support, not a guarantee";
  if (status === "needs_review") return "Possible H-1B sponsor match — awaiting review";
  if (status === "rejected") return "No confirmed employer match for this company";
  return "No H-1B sponsorship data available";
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

const LIST_COLUMNS_BASE =
  "id,name,company,email,stage,tier,mode,last_emailed,reply_status,classifier_status,dartmouth,notes,message_id,followup_date,created_at,state,company_intel_id";

// The company_intel embed must be a plain (left) join by default — most
// contacts have no company_intel row yet, and !inner would drop them from
// the unfiltered list. Only the sponsorsH1bOnly filter needs !inner, since
// PostgREST's `.eq()` on an embedded resource filters the embedded object,
// not the parent row set — !inner is what actually reduces which contacts
// come back.
function buildSelectColumns(filters: ContactsQueryFilters) {
  const joinType = filters.sponsorsH1bOnly ? "!inner" : "";
  return `${LIST_COLUMNS_BASE},company_intel${joinType}(sponsors_h1b,h1b_recent_count,match_status)`;
}

function buildContactsQuery(filters: ContactsQueryFilters, cursor: string | null) {
  const escaped = filters.nameOrCompany.trim().replace(/[%_]/g, "\\$&");

  // Select only columns needed for the list + sheet header; heavy text fields
  // (detail, job_description, job_title, etc.) are fetched on row-click.
  let q = supabase
    .from("contacts")
    .select(buildSelectColumns(filters))
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
  if (filters.sponsorsH1bOnly) q = q.eq("company_intel.sponsors_h1b", true);
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
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [company, setCompany] = useState("");
  const [role, setRole] = useState("");
  const [detail, setDetail] = useState("");
  const [resumeUrl, setResumeUrl] = useState("");
  const [jobTitle, setJobTitle] = useState("");
  const [contactState, setContactState] = useState("");
  const [connectionContext, setConnectionContext] = useState("");
  const [showPromoteModal, setShowPromoteModal] = useState(false);
  const [promoteTarget, setPromoteTarget] = useState<"outreach" | "applied">("outreach");
  const [promoting, setPromoting] = useState(false);

  const fetchIdRef = useRef(0);
  const prevFiltersRef = useRef<ContactsQueryFilters>(EMPTY_FILTERS);
  const sentinelRef = useRef<HTMLDivElement | null>(null);
  const listContainerRef = useRef<HTMLDivElement | null>(null);

  const openContact = useCallback(async (c: Contact) => {
    setSelectedContact(c);
    const { data } = await supabase.from("contacts").select("*").eq("id", c.id).single();
    if (data) setSelectedContact(data as Contact);
  }, []);

  // Sync sheet local state when selected contact changes
  useEffect(() => {
    setNotes(selectedContact?.notes ?? "");
    setName(selectedContact?.name ?? "");
    setEmail(selectedContact?.email ?? "");
    setCompany(selectedContact?.company ?? "");
    setRole(selectedContact?.role ?? "");
    setDetail(selectedContact?.detail ?? "");
    setResumeUrl(selectedContact?.resume_url ?? "");
    setJobTitle(selectedContact?.job_title ?? "");
    setContactState(selectedContact?.state ?? "");
    setConnectionContext(selectedContact?.connection_context ?? "");
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
      const rows = (data ?? []) as unknown as Contact[];
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
    const rows = (data ?? []) as unknown as Contact[];
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

  // ── Generic blur-save for text fields ───────────────────────────────────────

  async function handleBlurSave(
    field: keyof Contact,
    localValue: string,
    label: string,
    revert: () => void
  ) {
    if (!selectedContact) return;
    const original = (selectedContact[field] as string | null | undefined) ?? "";
    if (localValue === original) return;
    const newValue = localValue.trim() || null;
    const prev = selectedContact;
    const updated = { ...selectedContact, [field]: newValue };
    setSelectedContact(updated);
    setContacts((cs) => cs.map((c) => (c.id === updated.id ? updated : c)));
    const { error } = await supabase
      .from("contacts")
      .update({ [field]: newValue })
      .eq("id", selectedContact.id);
    if (error) {
      revert();
      setSelectedContact(prev);
      setContacts((cs) => cs.map((c) => (c.id === prev.id ? prev : c)));
      onError(`Failed to save ${label}: ${error.message}`);
    } else {
      onSuccess(`${label} saved`);
    }
  }

  // ── Mode change (immediate optimistic) ──────────────────────────────────────

  async function handleModeChange(mode: "outreach" | "applied" | "networking") {
    if (!selectedContact) return;
    const prev = selectedContact;
    const updated = { ...selectedContact, mode };
    setSelectedContact(updated);
    setContacts((cs) => cs.map((c) => (c.id === updated.id ? updated : c)));
    const { error } = await supabase
      .from("contacts")
      .update({ mode })
      .eq("id", selectedContact.id);
    if (error) {
      setSelectedContact(prev);
      setContacts((cs) => cs.map((c) => (c.id === prev.id ? prev : c)));
      onError(`Failed to update mode: ${error.message}`);
    }
  }

  // ── Dartmouth toggle (immediate optimistic) ──────────────────────────────────

  async function handleDartmouthChange(dartmouth: boolean) {
    if (!selectedContact) return;
    const prev = selectedContact;
    const updated = { ...selectedContact, dartmouth };
    setSelectedContact(updated);
    setContacts((cs) => cs.map((c) => (c.id === updated.id ? updated : c)));
    const { error } = await supabase
      .from("contacts")
      .update({ dartmouth })
      .eq("id", selectedContact.id);
    if (error) {
      setSelectedContact(prev);
      setContacts((cs) => cs.map((c) => (c.id === prev.id ? prev : c)));
      onError(`Failed to update: ${error.message}`);
    }
  }

  // ── State change (immediate optimistic) ─────────────────────────────────────

  async function handleStateChange(newState: string | null) {
    if (!selectedContact) return;
    const prev = selectedContact;
    const updated = { ...selectedContact, state: newState };
    setSelectedContact(updated);
    setContacts((cs) => cs.map((c) => (c.id === updated.id ? updated : c)));
    const { error } = await supabase
      .from("contacts")
      .update({ state: newState })
      .eq("id", selectedContact.id);
    if (error) {
      setSelectedContact(prev);
      setContacts((cs) => cs.map((c) => (c.id === prev.id ? prev : c)));
      onError(`Failed to update state: ${error.message}`);
    }
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

  // ── Promote networking contact to a role track ──────────────────────────────

  async function handlePromote() {
    if (!selectedContact) return;
    setPromoting(true);
    const today = new Date().toISOString().slice(0, 10);
    const annotation = `[Promoted from networking to ${promoteTarget} on ${today}]`;
    const payload = {
      mode: promoteTarget,
      stage: "new",
      followup_date: null,
      notes: selectedContact.notes ? `${selectedContact.notes}\n${annotation}` : annotation,
    };
    const { error } = await supabase
      .from("contacts")
      .update(payload)
      .eq("id", selectedContact.id);
    setPromoting(false);
    if (error) {
      onError(`Failed to promote contact: ${error.message}`);
      return;
    }
    const updated = { ...selectedContact, ...payload };
    setSelectedContact(updated);
    setContacts((cs) => cs.map((c) => (c.id === updated.id ? updated : c)));
    setShowPromoteModal(false);
    onSuccess(`Promoted to ${promoteTarget}`);
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
          <div>Visa</div>
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
              onClick={() => openContact(c)}
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
              <div className="flex items-center">
                <Tooltip content={signalTooltip(c.company_intel)}>
                  <span>
                    <Badge variant={signalVariant(c.company_intel)}>
                      {signalLabel(c.company_intel)}
                    </Badge>
                  </span>
                </Tooltip>
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
                        {selectedContact.mode === "outreach" && (
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
                        {selectedContact.mode === "applied" && (
                          <SelectGroup>
                            <SelectLabel>Applied</SelectLabel>
                            {APPLIED_STAGES.filter((s) => s !== "closed").map((s) => (
                              <SelectItem key={s} value={s}>
                                {stageLabel(s)}
                              </SelectItem>
                            ))}
                          </SelectGroup>
                        )}
                        {selectedContact.mode === "networking" && (
                          <SelectGroup>
                            <SelectLabel>Networking</SelectLabel>
                            {NETWORKING_STAGES.filter((s) => s !== "closed").map((s) => (
                              <SelectItem key={s} value={s}>
                                {stageLabel(s)}
                              </SelectItem>
                            ))}
                          </SelectGroup>
                        )}
                        <SelectGroup>
                          <SelectLabel>Reply</SelectLabel>
                          {REPLY_STAGES.map((s) => (
                            <SelectItem key={s} value={s}>
                              {stageLabel(s)}
                            </SelectItem>
                          ))}
                        </SelectGroup>
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

                {/* Contact details section — all fields editable */}
                <div className="space-y-3">
                  <div>
                    <label className="text-xs uppercase tracking-wider text-fg-dim mb-1.5 block">
                      Name
                    </label>
                    <TextInput
                      value={name}
                      onChange={(e) => setName(e.target.value)}
                      onBlur={() =>
                        handleBlurSave("name", name, "Name", () =>
                          setName(selectedContact.name ?? "")
                        )
                      }
                    />
                  </div>

                  <div>
                    <label className="text-xs uppercase tracking-wider text-fg-dim mb-1.5 block">
                      Email
                    </label>
                    <div className="flex items-center gap-2">
                      <TextInput
                        type="email"
                        value={email}
                        onChange={(e) => setEmail(e.target.value)}
                        onBlur={() =>
                          handleBlurSave("email", email, "Email", () =>
                            setEmail(selectedContact.email ?? "")
                          )
                        }
                        className="flex-1"
                      />
                      <button
                        type="button"
                        onClick={() => {
                          navigator.clipboard.writeText(email);
                          onSuccess("Email copied");
                        }}
                        className="text-fg-dim hover:text-fg p-1 rounded hover:bg-surface-2 shrink-0 transition-colors"
                        aria-label="Copy email"
                      >
                        <Copy className="size-3.5" />
                      </button>
                    </div>
                  </div>

                  <div>
                    <label className="text-xs uppercase tracking-wider text-fg-dim mb-1.5 block">
                      Company
                    </label>
                    <TextInput
                      value={company}
                      onChange={(e) => setCompany(e.target.value)}
                      onBlur={() =>
                        handleBlurSave("company", company, "Company", () =>
                          setCompany(selectedContact.company ?? "")
                        )
                      }
                    />
                  </div>

                  <div>
                    <label className="text-xs uppercase tracking-wider text-fg-dim mb-1.5 block">
                      Role
                    </label>
                    <TextInput
                      value={role}
                      onChange={(e) => setRole(e.target.value)}
                      onBlur={() =>
                        handleBlurSave("role", role, "Role", () =>
                          setRole(selectedContact.role ?? "")
                        )
                      }
                    />
                  </div>

                  <div>
                    <label className="text-xs uppercase tracking-wider text-fg-dim mb-1.5 block">
                      State
                    </label>
                    <Select
                      value={selectedContact.state ?? ""}
                      onValueChange={(v) => handleStateChange(v === "_none" ? null : (v || null))}
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
                  </div>

                  <div>
                    <label className="text-xs uppercase tracking-wider text-fg-dim mb-1.5 block">
                      Personalization hook
                    </label>
                    <TextArea
                      value={detail}
                      onChange={(e) => setDetail(e.target.value)}
                      onBlur={() =>
                        handleBlurSave("detail", detail, "Detail", () =>
                          setDetail(selectedContact.detail ?? "")
                        )
                      }
                      rows={3}
                    />
                  </div>

                  {selectedContact.mode === "applied" && (
                    <div>
                      <label className="text-xs uppercase tracking-wider text-fg-dim mb-1.5 block">
                        Job Title
                      </label>
                      <TextInput
                        value={jobTitle}
                        onChange={(e) => setJobTitle(e.target.value)}
                        onBlur={() =>
                          handleBlurSave("job_title", jobTitle, "Job Title", () =>
                            setJobTitle(selectedContact.job_title ?? "")
                          )
                        }
                      />
                    </div>
                  )}

                  {selectedContact.mode === "networking" && (
                    <div>
                      <label className="text-xs uppercase tracking-wider text-fg-dim mb-1.5 block">
                        Connection
                      </label>
                      <TextArea
                        value={connectionContext}
                        onChange={(e) => setConnectionContext(e.target.value)}
                        onBlur={() =>
                          handleBlurSave(
                            "connection_context",
                            connectionContext,
                            "Connection",
                            () => setConnectionContext(selectedContact.connection_context ?? "")
                          )
                        }
                        placeholder={
                          selectedContact.dartmouth
                            ? "e.g. Fellow Tuck/Thayer MEM, met at an alumni event"
                            : "e.g. Mutual contact, met at a conference"
                        }
                        rows={2}
                      />
                    </div>
                  )}

                  <div>
                    <label className="text-xs uppercase tracking-wider text-fg-dim mb-1.5 block">
                      Resume URL
                    </label>
                    <TextInput
                      value={resumeUrl}
                      onChange={(e) => setResumeUrl(e.target.value)}
                      onBlur={() =>
                        handleBlurSave("resume_url", resumeUrl, "Resume URL", () =>
                          setResumeUrl(selectedContact.resume_url ?? "")
                        )
                      }
                    />
                  </div>

                  <div>
                    <label className="text-xs uppercase tracking-wider text-fg-dim mb-1.5 block">
                      Mode
                    </label>
                    <div className="grid grid-cols-3 gap-1.5">
                      {(["outreach", "applied", "networking"] as const).map((m) => {
                        const active = (selectedContact.mode ?? "outreach") === m;
                        return (
                          <button
                            key={m}
                            type="button"
                            onClick={() => handleModeChange(m)}
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

                  {selectedContact.mode === "networking" && (
                    <div>
                      <button
                        type="button"
                        onClick={() => {
                          setPromoteTarget("outreach");
                          setShowPromoteModal(true);
                        }}
                        className="w-full rounded-lg border border-indigo-500/40 bg-indigo-500/10 text-indigo-300 py-2 text-xs hover:bg-indigo-500/20 transition-colors"
                      >
                        Promote to role track
                      </button>
                    </div>
                  )}

                  <ToggleSwitch
                    on={selectedContact.dartmouth ?? false}
                    onChange={handleDartmouthChange}
                    label="Dartmouth / Tuck / Thayer / Irving connection"
                  />

                  <div>
                    <label className="text-xs uppercase tracking-wider text-fg-dim mb-1.5 block">
                      Notes
                    </label>
                    <TextArea
                      value={notes}
                      onChange={(e) => setNotes(e.target.value)}
                      onBlur={() =>
                        handleBlurSave("notes", notes, "Notes", () =>
                          setNotes(selectedContact.notes ?? "")
                        )
                      }
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

      {/* ── Promote confirmation modal ───────────────────────────────────────── */}
      <ConfirmModal
        open={showPromoteModal}
        title="Promote to a role track?"
        body={
          <div className="space-y-3">
            <p>
              This moves {selectedContact?.name} off the networking track and
              starts a fresh outreach or applied sequence: stage resets to New
              and the follow-up date is cleared.
            </p>
            <div className="grid grid-cols-2 gap-1.5">
              {(["outreach", "applied"] as const).map((m) => (
                <button
                  key={m}
                  type="button"
                  onClick={() => setPromoteTarget(m)}
                  className={`rounded-lg border py-2 text-xs capitalize transition ${
                    promoteTarget === m
                      ? "border-indigo-500 bg-indigo-500/10 text-indigo-300"
                      : "border-border bg-surface text-fg-muted hover:border-border-strong"
                  }`}
                >
                  Promote to {m}
                </button>
              ))}
            </div>
          </div>
        }
        confirmLabel="Promote"
        confirmVariant="primary"
        loading={promoting}
        onCancel={() => setShowPromoteModal(false)}
        onConfirm={handlePromote}
      />
    </>
  );
}

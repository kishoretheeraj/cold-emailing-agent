"use client";

import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import { TextInput, TextArea } from "@/components/Field";
import {
  guessEmail,
  hookSuggestion,
  MAX_PEOPLE_PER_APPLICATION,
  modeFor,
  RELATIONSHIP_LABELS,
  RELATIONSHIPS,
  SCHOOLS,
  type Relationship,
  type SchoolId,
} from "@/lib/warmPaths";

type Person = {
  id: number | string;
  name: string | null;
  email: string | null;
  mode: string | null;
  stage: string | null;
  reply_status: string | null;
  relationship?: string | null;
  role?: string | null;
  blocker?: string | null;
};

type PeopleData = {
  application: { company: string; role: string };
  linked: Person[];
  known: Person[];
  known_emails: { name: string | null; email: string | null }[];
  search_links: { label: string; url: string }[];
  posting_emails: { email: string; kind: "person" | "inbox" }[];
  remaining: number;
  company_recent: number;
  company_cap: number;
  closed: boolean;
};

type Existing = { id: number | string; name: string | null; blocker: string | null };

const EMPTY_FORM = {
  name: "", email: "", relationship: "hiring_manager" as Relationship, title: "",
  school: "dartmouth" as SchoolId, tier: "2", linkedin: "", connection_context: "",
};

const selectClass = "w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm text-fg focus:border-indigo-500 focus:outline-none";

function relationshipLabel(value: string | null | undefined): string {
  return value && value in RELATIONSHIP_LABELS ? RELATIONSHIP_LABELS[value as Relationship] : "Linked";
}

// Warm paths (spec 2026-10-08-warm-paths-design §3.4): who the user knows or can find at the
// company, and adding the person they pick. The cold-email agent drafts to them overnight;
// applied-mode mail waits until the application has been submitted.
export function PeoplePanel({ applicationId, onChange }: { applicationId: string; onChange?: () => void }) {
  const [data, setData] = useState<PeopleData | null>(null);
  const [failed, setFailed] = useState(false);
  const [form, setForm] = useState(EMPTY_FORM);
  const [saving, setSaving] = useState(false);
  const [existing, setExisting] = useState<Existing | null>(null);

  const load = useCallback(async () => {
    try {
      const res = await fetch(`/api/applications/${applicationId}/people`);
      if (!res.ok) throw new Error(String(res.status));
      setData((await res.json()) as PeopleData);
      setFailed(false);
    } catch {
      setFailed(true);
    }
  }, [applicationId]);

  useEffect(() => {
    load();
  }, [load]);

  const changed = () => {
    load();
    onChange?.();
  };

  const link = async (contactId: number | string, action: "link" | "unlink") => {
    try {
      const res = await fetch(`/api/applications/${applicationId}/people/${action}`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ contact_id: contactId }),
      });
      const body = (await res.json().catch(() => ({}))) as { error?: string };
      if (res.ok) toast.success(action === "link" ? "Linked" : "Unlinked");
      else toast.error(body.error ?? "That did not work");
    } catch {
      toast.error("Could not reach the server");
    }
    setExisting(null);
    changed();
  };

  const add = async () => {
    setSaving(true);
    setExisting(null);
    try {
      const res = await fetch(`/api/applications/${applicationId}/people`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          ...form,
          tier: Number(form.tier),
          school: form.relationship === "alum" ? form.school : null,
          connection_context: modeFor(form.relationship) === "networking" ? form.connection_context : "",
        }),
      });
      const body = (await res.json().catch(() => ({}))) as { error?: string; existing?: Existing };
      if (res.ok) {
        toast.success(`${form.name} added. A draft will be ready tomorrow morning.`);
        setForm(EMPTY_FORM);
        changed();
      } else {
        toast.error(body.error ?? "Could not add this person");
        if (body.existing) setExisting(body.existing);
      }
    } catch {
      toast.error("Could not reach the server");
    }
    setSaving(false);
  };

  if (failed) return <p className="text-sm text-red-300">Could not load the people for this application.</p>;
  if (!data) return <p className="text-sm text-fg-dim">Loading people...</p>;

  const company = data.application.company;
  const networking = modeFor(form.relationship) === "networking";
  const suggestion = hookSuggestion(form.relationship, { company, school: form.school, title: form.title });
  const guess = form.name.trim() ? guessEmail(form.name, data.known_emails) : null;
  const full = data.remaining <= 0;
  const capped = data.company_recent >= data.company_cap;
  const set = (patch: Partial<typeof EMPTY_FORM>) => setForm((cur) => ({ ...cur, ...patch }));

  return (
    <section aria-label="People" className="flex flex-col gap-4 text-sm">
      <p className="text-fg-muted">
        Linked {data.linked.length} of {MAX_PEOPLE_PER_APPLICATION}. Drafts are created overnight and you send them.
        Mail to a hiring manager or recruiter waits until this application is submitted.
      </p>

      {data.linked.length > 0 && (
        <ul className="flex flex-col gap-2">
          {data.linked.map((p) => (
            <li key={p.id} className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-border px-3 py-2">
              <span className="text-fg">
                {p.name} <span className="text-fg-dim">&middot; {relationshipLabel(p.relationship)} &middot; {p.stage}
                  {p.reply_status && p.reply_status !== "no_reply" ? ` · ${p.reply_status}` : ""}</span>
              </span>
              <button type="button" onClick={() => link(p.id, "unlink")} className="text-xs text-fg-muted underline">Unlink</button>
            </li>
          ))}
        </ul>
      )}

      {data.closed && <p className="text-amber-300">This application is closed, so nobody new is linked to it.</p>}

      {data.known.length > 0 && (
        <div className="flex flex-col gap-1">
          <h4 className="font-medium text-fg">Already in your contacts at {company}</h4>
          <ul className="flex flex-col gap-1">
            {data.known.map((p) => (
              <li key={p.id} className="flex flex-wrap items-center justify-between gap-2">
                <span className="text-fg-muted">{p.name}{p.role ? `, ${p.role}` : ""}</span>
                {p.blocker ? (
                  <span className="text-xs text-fg-dim">{p.blocker}</span>
                ) : (
                  <button type="button" onClick={() => link(p.id, "link")} disabled={full || data.closed}
                    className="rounded-md border border-border-strong px-2 py-1 text-xs text-fg disabled:opacity-50">Link</button>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="flex flex-col gap-1">
        <h4 className="font-medium text-fg">Find people</h4>
        <ul className="flex flex-wrap gap-x-4 gap-y-1">
          {data.search_links.map((l) => (
            <li key={l.url}><a href={l.url} target="_blank" rel="noreferrer" className="text-indigo-300 underline">{l.label}</a></li>
          ))}
        </ul>
        {data.posting_emails.length > 0 && (
          <p className="text-fg-muted">
            In the posting:{" "}
            {data.posting_emails.map((e) => (
              <span key={e.email} className="mr-2">
                {e.kind === "person" ? (
                  <button type="button" onClick={() => set({ email: e.email })} className="text-indigo-300 underline">{e.email}</button>
                ) : (
                  <>{e.email} <span className="text-fg-dim">(inbox)</span></>
                )}
              </span>
            ))}
          </p>
        )}
      </div>

      {!data.closed && (
        <form aria-label="Add person" className="grid gap-2 sm:grid-cols-2"
          onSubmit={(e) => { e.preventDefault(); add(); }}>
          <TextInput aria-label="Name" placeholder="Name" value={form.name} required
            onChange={(e) => set({ name: e.target.value })} />
          <div className="flex flex-col gap-1">
            <TextInput aria-label="Email" placeholder="Email" type="email" value={form.email} required
              onChange={(e) => set({ email: e.target.value })} />
            {guess && !form.email && (
              <button type="button" onClick={() => set({ email: guess.email })} className="self-start text-xs text-indigo-300 underline">
                Use {guess.email} (guessed from {guess.basis} known addresses; unverified)
              </button>
            )}
          </div>
          <select aria-label="Relationship" className={selectClass} value={form.relationship}
            onChange={(e) => set({ relationship: e.target.value as Relationship })}>
            {RELATIONSHIPS.map((r) => <option key={r} value={r}>{RELATIONSHIP_LABELS[r]}</option>)}
          </select>
          <TextInput aria-label="Their title" placeholder="Their title (optional)" value={form.title}
            onChange={(e) => set({ title: e.target.value })} />
          {form.relationship === "alum" && (
            <select aria-label="School" className={selectClass} value={form.school}
              onChange={(e) => set({ school: e.target.value as SchoolId })}>
              {SCHOOLS.map((s) => <option key={s.id} value={s.id}>{s.label}</option>)}
            </select>
          )}
          <select aria-label="Tier" className={selectClass} value={form.tier} onChange={(e) => set({ tier: e.target.value })}>
            <option value="1">Tier 1 (research and critic)</option>
            <option value="2">Tier 2</option>
            <option value="3">Tier 3</option>
          </select>
          <TextInput aria-label="LinkedIn URL" placeholder="LinkedIn URL (optional)" value={form.linkedin}
            onChange={(e) => set({ linkedin: e.target.value })} className="sm:col-span-2" />
          {networking && (
            <div className="flex flex-col gap-1 sm:col-span-2">
              <TextArea aria-label="Connection" placeholder={suggestion || "What you have in common (leave empty if nothing)"}
                value={form.connection_context} onChange={(e) => set({ connection_context: e.target.value })}
                className="min-h-[3rem]" />
              {suggestion && !form.connection_context && (
                <button type="button" onClick={() => set({ connection_context: suggestion })}
                  className="self-start text-xs text-indigo-300 underline">Use suggestion</button>
              )}
            </div>
          )}
          {existing && !existing.blocker && (
            <button type="button" onClick={() => link(existing.id, "link")}
              className="rounded-md border border-border-strong px-3 py-2 text-fg sm:col-span-2">Link {existing.name} instead</button>
          )}
          <div className="flex flex-wrap items-center gap-3 sm:col-span-2">
            <button type="submit" disabled={saving || full || capped}
              className="rounded-md bg-indigo-600 px-4 py-2 font-medium text-white disabled:opacity-50">Add person</button>
            {full && <span className="text-fg-dim">This application has {MAX_PEOPLE_PER_APPLICATION} people.</span>}
            {!full && capped && <span className="text-fg-dim">You have added {data.company_cap} people at {company} in the last 30 days.</span>}
          </div>
        </form>
      )}
    </section>
  );
}

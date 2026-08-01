"use client";

import { useState } from "react";
import { supabase, resolveInsertError, checkDuplicateEmails } from "@/lib/supabase";
import { US_STATES } from "@/lib/timezone";
import {
  Label,
  TextInput,
  TextArea,
  ToggleSwitch,
  TierSelector,
} from "./Field";
import {
  Select,
  SelectTrigger,
  SelectValue,
  SelectContent,
  SelectItem,
} from "@/components/ui/Select";

type FormMode = "outreach" | "applied" | "networking";

export function StructuredForm({
  onAdded,
  onError,
}: {
  onAdded: () => void;
  onError: (msg: string) => void;
}) {
  const [section, setSection] = useState<FormMode>("outreach");

  return (
    <div className="space-y-5">
      <div className="inline-flex rounded-lg border border-border bg-surface p-1">
        {(["outreach", "applied", "networking"] as const).map((m) => {
          const active = section === m;
          const label =
            m === "outreach"
              ? "Outreach Contact"
              : m === "applied"
              ? "Applied Contact"
              : "Networking Contact";
          return (
            <button
              key={m}
              onClick={() => setSection(m)}
              className={`rounded-md px-4 py-1.5 text-xs font-medium capitalize transition ${
                active
                  ? "bg-indigo-500 text-white"
                  : "text-fg-muted hover:text-fg"
              }`}
            >
              {label}
            </button>
          );
        })}
      </div>

      {section === "outreach" && <OutreachForm onAdded={onAdded} onError={onError} />}
      {section === "applied" && <AppliedForm onAdded={onAdded} onError={onError} />}
      {section === "networking" && <NetworkingForm onAdded={onAdded} onError={onError} />}
    </div>
  );
}

function OutreachForm({
  onAdded,
  onError,
}: {
  onAdded: () => void;
  onError: (msg: string) => void;
}) {
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [company, setCompany] = useState("");
  const [role, setRole] = useState("");
  const [detail, setDetail] = useState("");
  const [tier, setTier] = useState(2);
  const [dartmouth, setDartmouth] = useState(false);
  const [notes, setNotes] = useState("");
  const [resumeUrl, setResumeUrl] = useState("");
  const [contactState, setContactState] = useState("");
  const [saving, setSaving] = useState(false);

  async function submit() {
    if (!name.trim() || !email.trim() || !company.trim()) {
      onError("Name, Email, and Company are required");
      return;
    }
    setSaving(true);
    try {
      const dupes = await checkDuplicateEmails([email]);
      if (dupes.size > 0) throw new Error("A contact with this email is already in your list.");
      const { error } = await supabase.from("contacts").insert({
        name,
        email,
        company,
        role: role || null,
        detail: detail || null,
        tier,
        mode: "outreach",
        dartmouth,
        notes: notes || null,
        resume_url: resumeUrl || null,
        state: contactState || null,
        stage: "new",
        reply_status: "no_reply",
      });
      if (error) throw new Error(await resolveInsertError(error, email));
      setName("");
      setEmail("");
      setCompany("");
      setRole("");
      setDetail("");
      setTier(2);
      setDartmouth(false);
      setNotes("");
      setResumeUrl("");
      setContactState("");
      onAdded();
    } catch (err) {
      onError(err instanceof Error ? err.message : "save failed");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="rounded-xl border border-border bg-surface-2 p-5 space-y-4">
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <div>
          <Label required>Name</Label>
          <TextInput value={name} onChange={(e) => setName(e.target.value)} />
        </div>
        <div>
          <Label required>Email</Label>
          <TextInput
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
        </div>
        <div>
          <Label required>Company</Label>
          <TextInput
            value={company}
            onChange={(e) => setCompany(e.target.value)}
          />
        </div>
        <div>
          <Label>Role</Label>
          <TextInput value={role} onChange={(e) => setRole(e.target.value)} />
        </div>
      </div>

      <div>
        <Label>Detail (one specific thing to reference)</Label>
        <TextArea
          value={detail}
          onChange={(e) => setDetail(e.target.value)}
          rows={3}
        />
      </div>

      <div>
        <Label>State</Label>
        <Select
          value={contactState}
          onValueChange={(v) => setContactState(v === "_none" ? "" : v)}
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
        <Label>Tier</Label>
        <TierSelector value={tier} onChange={setTier} />
      </div>

      <ToggleSwitch
        on={dartmouth}
        onChange={setDartmouth}
        label="Dartmouth / Tuck / Thayer / Irving connection"
      />

      <div>
        <Label>Notes</Label>
        <TextArea
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          rows={2}
        />
      </div>

      <div>
        <Label>Resume link (optional)</Label>
        <TextInput
          value={resumeUrl}
          onChange={(e) => setResumeUrl(e.target.value)}
          placeholder="Google Drive URL"
        />
      </div>

      <div className="flex justify-end pt-1">
        <button
          onClick={submit}
          disabled={saving}
          className="rounded-lg bg-indigo-500 px-4 py-2 text-sm font-medium text-white transition hover:bg-indigo-400 disabled:bg-indigo-500/30"
        >
          {saving ? "Saving…" : "Add Contact"}
        </button>
      </div>
    </div>
  );
}

function AppliedForm({
  onAdded,
  onError,
}: {
  onAdded: () => void;
  onError: (msg: string) => void;
}) {
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [company, setCompany] = useState("");
  const [role, setRole] = useState("");
  const [jobTitle, setJobTitle] = useState("");
  const [appliedDate, setAppliedDate] = useState("");
  const [jobDescription, setJobDescription] = useState("");
  const [dartmouth, setDartmouth] = useState(false);
  const [notes, setNotes] = useState("");
  const [resumeUrl, setResumeUrl] = useState("");
  const [saving, setSaving] = useState(false);

  async function submit() {
    if (
      !name.trim() ||
      !email.trim() ||
      !company.trim() ||
      !jobTitle.trim() ||
      !appliedDate.trim()
    ) {
      onError("Name, Email, Company, Job Title, and Applied Date are required");
      return;
    }
    setSaving(true);
    try {
      const dupes = await checkDuplicateEmails([email]);
      if (dupes.size > 0) throw new Error("A contact with this email is already in your list.");
      const { error } = await supabase.from("contacts").insert({
        name,
        email,
        company,
        role: role || null,
        job_title: jobTitle,
        applied_date: appliedDate,
        job_description: jobDescription || null,
        mode: "applied",
        dartmouth,
        notes: notes || null,
        resume_url: resumeUrl || null,
        stage: "new",
        reply_status: "no_reply",
      });
      if (error) throw new Error(await resolveInsertError(error, email));
      setName("");
      setEmail("");
      setCompany("");
      setRole("");
      setJobTitle("");
      setAppliedDate("");
      setJobDescription("");
      setDartmouth(false);
      setNotes("");
      setResumeUrl("");
      onAdded();
    } catch (err) {
      onError(err instanceof Error ? err.message : "save failed");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="rounded-xl border border-border bg-surface-2 p-5 space-y-4">
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <div>
          <Label required>Name</Label>
          <TextInput value={name} onChange={(e) => setName(e.target.value)} />
        </div>
        <div>
          <Label required>Email</Label>
          <TextInput
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
        </div>
        <div>
          <Label required>Company</Label>
          <TextInput
            value={company}
            onChange={(e) => setCompany(e.target.value)}
          />
        </div>
        <div>
          <Label>Their Role / Title</Label>
          <TextInput value={role} onChange={(e) => setRole(e.target.value)} />
        </div>
        <div>
          <Label required>Job Title Applied For</Label>
          <TextInput
            value={jobTitle}
            onChange={(e) => setJobTitle(e.target.value)}
          />
        </div>
        <div>
          <Label required>Applied Date</Label>
          <TextInput
            type="date"
            value={appliedDate}
            onChange={(e) => setAppliedDate(e.target.value)}
          />
        </div>
      </div>

      <div>
        <Label>Job Description (paste full JD here)</Label>
        <TextArea
          value={jobDescription}
          onChange={(e) => setJobDescription(e.target.value)}
          rows={8}
        />
      </div>

      <ToggleSwitch
        on={dartmouth}
        onChange={setDartmouth}
        label="Dartmouth / Tuck / Thayer / Irving connection"
      />

      <div>
        <Label>Notes</Label>
        <TextArea
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          rows={2}
        />
      </div>

      <div>
        <Label>Resume link (optional)</Label>
        <TextInput
          value={resumeUrl}
          onChange={(e) => setResumeUrl(e.target.value)}
          placeholder="Google Drive URL"
        />
      </div>

      <div className="flex justify-end pt-1">
        <button
          onClick={submit}
          disabled={saving}
          className="rounded-lg bg-indigo-500 px-4 py-2 text-sm font-medium text-white transition hover:bg-indigo-400 disabled:bg-indigo-500/30"
        >
          {saving ? "Saving…" : "Add Contact"}
        </button>
      </div>
    </div>
  );
}

function NetworkingForm({
  onAdded,
  onError,
}: {
  onAdded: () => void;
  onError: (msg: string) => void;
}) {
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [company, setCompany] = useState("");
  const [role, setRole] = useState("");
  const [connectionContext, setConnectionContext] = useState("");
  const [tier, setTier] = useState(2);
  const [dartmouth, setDartmouth] = useState(false);
  const [notes, setNotes] = useState("");
  const [resumeUrl, setResumeUrl] = useState("");
  const [saving, setSaving] = useState(false);

  async function submit() {
    if (!name.trim() || !email.trim() || !company.trim()) {
      onError("Name, Email, and Company are required");
      return;
    }
    setSaving(true);
    try {
      const dupes = await checkDuplicateEmails([email]);
      if (dupes.size > 0) throw new Error("A contact with this email is already in your list.");
      const { error } = await supabase.from("contacts").insert({
        name,
        email,
        company,
        role: role || null,
        connection_context: connectionContext || null,
        tier,
        mode: "networking",
        dartmouth,
        notes: notes || null,
        resume_url: resumeUrl || null,
        stage: "new",
        reply_status: "no_reply",
      });
      if (error) throw new Error(await resolveInsertError(error, email));
      setName("");
      setEmail("");
      setCompany("");
      setRole("");
      setConnectionContext("");
      setTier(2);
      setDartmouth(false);
      setNotes("");
      setResumeUrl("");
      onAdded();
    } catch (err) {
      onError(err instanceof Error ? err.message : "save failed");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="rounded-xl border border-border bg-surface-2 p-5 space-y-4">
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <div>
          <Label required>Name</Label>
          <TextInput value={name} onChange={(e) => setName(e.target.value)} />
        </div>
        <div>
          <Label required>Email</Label>
          <TextInput
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
        </div>
        <div>
          <Label required>Company</Label>
          <TextInput
            value={company}
            onChange={(e) => setCompany(e.target.value)}
          />
        </div>
        <div>
          <Label>Role</Label>
          <TextInput value={role} onChange={(e) => setRole(e.target.value)} />
        </div>
      </div>

      <div>
        <Label>Connection (the hook to lead with — never fabricated if left blank)</Label>
        <TextArea
          value={connectionContext}
          onChange={(e) => setConnectionContext(e.target.value)}
          placeholder="e.g. Fellow Tuck/Thayer MEM, met at an alumni event, mutual contact"
          rows={2}
        />
      </div>

      <div>
        <Label>Tier</Label>
        <TierSelector value={tier} onChange={setTier} />
      </div>

      <ToggleSwitch
        on={dartmouth}
        onChange={setDartmouth}
        label="Dartmouth / Tuck / Thayer / Irving connection"
      />

      <div>
        <Label>Notes</Label>
        <TextArea
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          rows={2}
        />
      </div>

      <div>
        <Label>Resume link (optional)</Label>
        <TextInput
          value={resumeUrl}
          onChange={(e) => setResumeUrl(e.target.value)}
          placeholder="Google Drive URL"
        />
      </div>

      <div className="flex justify-end pt-1">
        <button
          onClick={submit}
          disabled={saving}
          className="rounded-lg bg-indigo-500 px-4 py-2 text-sm font-medium text-white transition hover:bg-indigo-400 disabled:bg-indigo-500/30"
        >
          {saving ? "Saving…" : "Add Contact"}
        </button>
      </div>
    </div>
  );
}

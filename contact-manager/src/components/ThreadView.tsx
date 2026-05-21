"use client";

import { useEffect, useState } from "react";
import { supabase } from "@/lib/supabase";
import type { EmailMessage } from "@/lib/types";

const TRUNCATE_AT = 300;

// ── Body sanitization ──────────────────────────────────────────────────────────

const MIME_BOUNDARY_RE = /^-{4}[_\w]/;

function sanitizeBody(raw: string): { display: string; garbled: boolean } {
  const trimmed = raw.trimStart();
  // Raw MIME structure (Samsung Galaxy and similar multipart/mixed emails stored
  // via the old BODY[TEXT] code path that didn't decode transfer-encoding).
  if (MIME_BOUNDARY_RE.test(trimmed) && trimmed.includes("Content-Type:")) {
    return { display: "", garbled: true };
  }
  // Raw HTML with quoted-printable artifacts from the same old code path.
  if (trimmed.startsWith("<!DOCTYPE") || trimmed.toLowerCase().startsWith("<html")) {
    const stripped = trimmed
      .replace(/=\r?\n/g, "")
      .replace(/=([0-9A-Fa-f]{2})/g, (_, h) => String.fromCharCode(parseInt(h, 16)))
      .replace(/<[^>]+>/g, " ")
      .replace(/\s{2,}/g, " ")
      .trim();
    return { display: stripped, garbled: false };
  }
  return { display: raw, garbled: false };
}

const STAGE_LABELS: Record<string, string> = {
  new: "First Touch",
  applied_new: "Applied Intro",
  first_touch_sent: "Follow-up 1",
  followup1_sent: "Follow-up 2",
  followup2_sent: "Breakup",
  applied_intro_sent: "Applied Follow-up",
  reply_drafted: "Reply",
};

function MessageBubble({ msg }: { msg: EmailMessage }) {
  const [expanded, setExpanded] = useState(false);
  const isOut = msg.direction === "outgoing";
  const { display: body, garbled } = sanitizeBody(msg.body ?? "");
  const truncated = body.length > TRUNCATE_AT && !expanded;
  const stageLabel = isOut && msg.stage_at_send ? STAGE_LABELS[msg.stage_at_send] : null;

  return (
    <div className={`flex flex-col gap-1 ${isOut ? "items-end" : "items-start"}`}>
      <div className="flex items-center gap-2 text-xs text-fg-dim flex-wrap">
        <span>{isOut ? "You" : "Them"}</span>
        {stageLabel && (
          <>
            <span>·</span>
            <span className="text-indigo-400 font-medium">{stageLabel}</span>
          </>
        )}
        <span>·</span>
        <span>
          {new Date(msg.sent_at).toLocaleDateString("en-US", {
            month: "short",
            day: "numeric",
            year: "numeric",
          })}
        </span>
        {msg.subject && (
          <>
            <span>·</span>
            <span className="truncate max-w-[160px]">{msg.subject}</span>
          </>
        )}
      </div>
      <div
        className={`rounded-lg px-3 py-2 text-sm max-w-[90%] whitespace-pre-wrap break-words ${
          isOut
            ? "bg-indigo-500/20 text-indigo-100 border border-indigo-500/30"
            : "bg-surface-2 text-fg border border-border"
        }`}
      >
        {garbled ? (
          <span className="italic text-fg-dim text-xs">
            (Message encoding not supported —{" "}
            <button
              type="button"
              onClick={() => window.open("https://mail.google.com/mail/u/0/#inbox", "_blank")}
              className="underline hover:text-fg transition-colors"
            >
              open in Gmail
            </button>
            )
          </span>
        ) : (
          <>
            {truncated ? body.slice(0, TRUNCATE_AT) + "…" : body}
            {body.length > TRUNCATE_AT && (
              <button
                type="button"
                onClick={() => setExpanded((e) => !e)}
                className="block mt-1 text-xs text-fg-dim hover:text-fg transition-colors"
              >
                {expanded ? "Show less" : "Show more"}
              </button>
            )}
          </>
        )}
      </div>
    </div>
  );
}

type Props = { contactId: string | number };

export function ThreadView({ contactId }: Props) {
  const [messages, setMessages] = useState<EmailMessage[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    supabase
      .from("email_messages")
      .select("*")
      .eq("contact_id", contactId)
      .order("sent_at", { ascending: true })
      .then(({ data }) => {
        setMessages((data as EmailMessage[]) ?? []);
        setLoading(false);
      });
  }, [contactId]);

  if (loading) {
    return <p className="text-xs text-fg-dim py-2">Loading thread...</p>;
  }

  if (messages.length === 0) {
    return (
      <p className="text-xs text-fg-dim py-2">
        No emails recorded yet.
      </p>
    );
  }

  return (
    <div className="flex flex-col gap-3 pt-1">
      {messages.map((m) => (
        <MessageBubble key={m.id} msg={m} />
      ))}
    </div>
  );
}

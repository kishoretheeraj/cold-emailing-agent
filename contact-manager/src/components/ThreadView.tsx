"use client";

import { useEffect, useState } from "react";
import { supabase } from "@/lib/supabase";
import type { EmailMessage } from "@/lib/types";

const TRUNCATE_AT = 300;

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
  const body = msg.body ?? "";
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

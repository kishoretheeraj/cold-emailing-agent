"use client";

import { Skeleton } from "@/components/ui/Skeleton";
import { EmptyState } from "@/components/ui/EmptyState";
import { Badge } from "@/components/ui/Badge";
import { FileText } from "lucide-react";

// ── Types ──────────────────────────────────────────────────────────────────────

export type WriterPreview = {
  kind: "writer";
  body: string;
  subject?: string;
};

export type CriticPreview = {
  kind: "critic";
  score: number;
  verdict: string;
  feedback: string;
  killed_by: string[];
  failed_soft_criteria: string[];
  rewrite_required: boolean;
};

export type PreviewResult = WriterPreview | CriticPreview;

// ── Props ──────────────────────────────────────────────────────────────────────

type Props = {
  sandboxPreview: PreviewResult | null;
  savedPreview: PreviewResult | null;    // non-null only when sandbox differs from saved
  sandboxLoading: boolean;
  savedLoading: boolean;
  contactSelected: boolean;
};

// ── Component ──────────────────────────────────────────────────────────────────

export function LabPreviewPanel({
  sandboxPreview,
  savedPreview,
  sandboxLoading,
  savedLoading,
  contactSelected,
}: Props) {
  const showCompare = savedPreview !== null;

  if (!contactSelected) {
    return (
      <EmptyState
        icon={<FileText className="size-5 text-fg-muted" />}
        title="No contact selected"
        description="Pick a contact from the picker above, then click Preview."
      />
    );
  }

  if (!sandboxPreview && !sandboxLoading) {
    return (
      <EmptyState
        icon={<FileText className="size-5 text-fg-muted" />}
        title="No preview yet"
        description="Edit a prompt and click Preview to see what the agent would generate."
      />
    );
  }

  if (showCompare) {
    return (
      <div className="grid grid-cols-2 gap-4 h-full">
        <div className="flex flex-col gap-2">
          <div className="text-xs font-medium text-fg-muted uppercase tracking-wider">
            Saved
          </div>
          {savedLoading ? (
            <LoadingSkeletons />
          ) : savedPreview ? (
            <PreviewCard result={savedPreview} dimmed />
          ) : null}
        </div>
        <div className="flex flex-col gap-2">
          <div className="text-xs font-medium text-indigo-400 uppercase tracking-wider">
            Sandbox
          </div>
          {sandboxLoading ? (
            <LoadingSkeletons />
          ) : sandboxPreview ? (
            <PreviewCard result={sandboxPreview} highlighted />
          ) : null}
        </div>
      </div>
    );
  }

  // Single column
  return (
    <div className="flex flex-col gap-2 h-full">
      {sandboxLoading ? <LoadingSkeletons /> : sandboxPreview ? <PreviewCard result={sandboxPreview} /> : null}
    </div>
  );
}

// ── Sub-components ─────────────────────────────────────────────────────────────

function LoadingSkeletons() {
  return (
    <div className="space-y-2">
      <Skeleton className="h-4 w-32" />
      <Skeleton className="h-3 w-full" />
      <Skeleton className="h-3 w-full" />
      <Skeleton className="h-3 w-4/5" />
      <Skeleton className="h-3 w-full" />
      <Skeleton className="h-3 w-3/4" />
    </div>
  );
}

function PreviewCard({
  result,
  dimmed = false,
  highlighted = false,
}: {
  result: PreviewResult;
  dimmed?: boolean;
  highlighted?: boolean;
}) {
  const containerClass = [
    "rounded-xl border p-4 flex-1 overflow-y-auto",
    dimmed ? "border-border/50 bg-surface/60 opacity-70" : "border-border bg-surface",
    highlighted ? "ring-1 ring-indigo-500/20" : "",
  ]
    .filter(Boolean)
    .join(" ");

  if (result.kind === "writer") {
    return (
      <div className={containerClass}>
        {result.subject && (
          <div className="mb-3 pb-3 border-b border-border">
            <div className="text-xs uppercase tracking-wider text-fg-dim mb-1">Subject</div>
            <div className="text-sm font-medium text-fg">{result.subject}</div>
          </div>
        )}
        <div className="text-xs uppercase tracking-wider text-fg-dim mb-2">Body</div>
        <pre className="text-sm text-fg whitespace-pre-wrap font-sans leading-relaxed">
          {result.body}
        </pre>
      </div>
    );
  }

  // Critic result
  const verdictVariant =
    result.verdict === "PASS" ? "emerald" : "red";

  return (
    <div className={containerClass}>
      {/* Score + Verdict */}
      <div className="flex items-center gap-3 mb-4">
        <div className="text-3xl font-bold text-fg">{result.score}</div>
        <div>
          <Badge variant={verdictVariant}>{result.verdict}</Badge>
          {result.rewrite_required && (
            <div className="text-xs text-amber-400 mt-0.5">Rewrite required</div>
          )}
        </div>
      </div>

      {/* Feedback */}
      {result.feedback && (
        <div className="mb-4">
          <div className="text-xs uppercase tracking-wider text-fg-dim mb-1">Feedback</div>
          <p className="text-sm text-fg">{result.feedback}</p>
        </div>
      )}

      {/* Killed by */}
      {result.killed_by.length > 0 && (
        <div className="mb-3">
          <div className="text-xs uppercase tracking-wider text-fg-dim mb-1">Killed by</div>
          <div className="flex flex-wrap gap-1.5">
            {result.killed_by.map((k) => (
              <Badge key={k} variant="red">{k}</Badge>
            ))}
          </div>
        </div>
      )}

      {/* Failed soft criteria */}
      {result.failed_soft_criteria.length > 0 && (
        <div>
          <div className="text-xs uppercase tracking-wider text-fg-dim mb-1">
            Failed soft criteria
          </div>
          <div className="flex flex-wrap gap-1.5">
            {result.failed_soft_criteria.map((k) => (
              <Badge key={k} variant="amber">{k}</Badge>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

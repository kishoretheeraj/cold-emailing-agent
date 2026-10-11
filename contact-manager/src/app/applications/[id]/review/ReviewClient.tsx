"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import ReviewDetail from "@/components/ReviewDetail";
import type { JobApplication } from "@/lib/types";

type ReviewableApplication = JobApplication & { preview_revision_hash?: string | null };

export default function ReviewClient({ app }: { app: ReviewableApplication }) {
  const router = useRouter();
  const [approving, setApproving] = useState(false);

  async function handleApprove(target: JobApplication) {
    setApproving(true);
    try {
      const res = await fetch(`/api/applications/${target.id}/submit`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ revision_hash: app.preview_revision_hash }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => null);
        toast.error(body?.error ?? "Approval failed");
        return;
      }
      toast.success("Approved. Submitting shortly.");
      router.back();
    } catch {
      toast.error("Approval failed");
    } finally {
      setApproving(false);
    }
  }

  async function handleReject(target: JobApplication) {
    try {
      const res = await fetch(`/api/applications/${target.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ stage: "withdrawn" }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => null);
        toast.error(body?.error ?? "Reject failed");
        return;
      }
      toast.success("Application skipped");
      router.back();
    } catch {
      toast.error("Reject failed");
    }
  }

  return (
    <ReviewDetail
      app={app}
      approving={approving}
      onApprove={handleApprove}
      onReject={handleReject}
      onBack={() => router.back()}
    />
  );
}

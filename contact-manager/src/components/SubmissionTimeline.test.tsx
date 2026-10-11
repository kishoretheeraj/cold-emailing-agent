import { describe, it, expect } from "vitest";
import { render, screen, within } from "@testing-library/react";
import SubmissionTimeline, { getTimelineStages } from "./SubmissionTimeline";
import type { JobApplication } from "@/lib/types";

function makeApp(overrides: Record<string, unknown> = {}): JobApplication {
  return {
    id: "1",
    company: "Acme",
    role: "Product Manager",
    approved_at: null,
    submit_attempted_at: null,
    updated_at: "2026-10-11T15:00:00Z",
    submission_evidence: null,
    automation_status: "idle",
    apply_blocked_reason: null,
    ...overrides,
  } as unknown as JobApplication;
}

function states(app: JobApplication) {
  return getTimelineStages(app).map((s) => s.state);
}

describe("getTimelineStages", () => {
  it("returns the four stages in order", () => {
    expect(getTimelineStages(makeApp()).map((s) => s.key)).toEqual([
      "approved",
      "queued",
      "applying",
      "confirmed",
    ]);
  });

  it("just approved: queued is in progress waiting for worker", () => {
    const app = makeApp({
      approved_at: "2026-10-11T14:00:00Z",
      automation_status: "approved",
    });
    const stages = getTimelineStages(app);
    expect(states(app)).toEqual(["completed", "in-progress", "pending", "pending"]);
    expect(stages[0].detail).toBe("Approved by you");
    expect(stages[1].detail).toBe("Waiting for worker");
    expect(stages[1].at).toBe("2026-10-11T14:00:00Z");
  });

  it("submitting: applying is in progress", () => {
    const app = makeApp({
      approved_at: "2026-10-11T14:00:00Z",
      submit_attempted_at: "2026-10-11T14:05:00Z",
      automation_status: "submitting",
    });
    const stages = getTimelineStages(app);
    expect(states(app)).toEqual(["completed", "completed", "in-progress", "pending"]);
    expect(stages[1].detail).toBe("Picked up");
    expect(stages[2].detail).toBe("Applying…");
    expect(stages[2].at).toBe("2026-10-11T14:05:00Z");
  });

  it("submitted with evidence url: all completed, confirmed uses evidence time", () => {
    const app = makeApp({
      approved_at: "2026-10-11T14:00:00Z",
      submit_attempted_at: "2026-10-11T14:05:00Z",
      automation_status: "submitted",
      submission_evidence: {
        url: "https://example.com/thanks",
        at: "2026-10-11T14:06:00Z",
      },
    });
    const stages = getTimelineStages(app);
    expect(states(app)).toEqual(["completed", "completed", "completed", "completed"]);
    expect(stages[2].detail).toBe("Submitted to ATS");
    expect(stages[3].at).toBe("2026-10-11T14:06:00Z");
    expect(stages[3].detail).toBe("View confirmation");
  });

  it("submitted without evidence falls back to updated_at", () => {
    const app = makeApp({
      approved_at: "2026-10-11T14:00:00Z",
      automation_status: "submitted",
    });
    expect(getTimelineStages(app)[3].at).toBe("2026-10-11T15:00:00Z");
  });

  it("failed_retryable: confirmed stage failed", () => {
    const app = makeApp({
      approved_at: "2026-10-11T14:00:00Z",
      submit_attempted_at: "2026-10-11T14:05:00Z",
      automation_status: "failed_retryable",
      apply_blocked_reason: "Form changed after approval",
    });
    const stages = getTimelineStages(app);
    expect(states(app)).toEqual(["completed", "completed", "completed", "failed"]);
    expect(stages[3].detail).toBe("Needs attention");
  });

  it("needs_confirmation: confirmed is in progress and labelled Confirming", () => {
    const app = makeApp({
      approved_at: "2026-10-11T14:00:00Z",
      submit_attempted_at: "2026-10-11T14:05:00Z",
      automation_status: "needs_confirmation",
    });
    const stages = getTimelineStages(app);
    expect(states(app)).toEqual(["completed", "completed", "completed", "in-progress"]);
    expect(stages[3].label).toBe("Confirming");
  });
});

describe("SubmissionTimeline", () => {
  it("renders four stages and the confirmation link", () => {
    render(
      <SubmissionTimeline
        app={makeApp({
          approved_at: "2026-10-11T14:00:00Z",
          submit_attempted_at: "2026-10-11T14:05:00Z",
          automation_status: "submitted",
          submission_evidence: { url: "https://example.com/thanks" },
        })}
      />,
    );
    const list = screen.getByRole("list", { name: "Submission timeline" });
    expect(within(list).getAllByRole("listitem")).toHaveLength(4);
    const link = screen.getByRole("link", { name: "View confirmation" });
    expect(link).toHaveAttribute("href", "https://example.com/thanks");
  });

  it("does not link a non-http evidence url", () => {
    render(
      <SubmissionTimeline
        app={makeApp({
          approved_at: "2026-10-11T14:00:00Z",
          automation_status: "submitted",
          submission_evidence: { url: "javascript:alert(1)" },
        })}
      />,
    );
    expect(screen.queryByRole("link")).toBeNull();
  });

  it("shows the blocked reason when failed", () => {
    render(
      <SubmissionTimeline
        app={makeApp({
          approved_at: "2026-10-11T14:00:00Z",
          automation_status: "failed_terminal",
          apply_blocked_reason: "Posting closed",
        })}
      />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("Needs attention");
    expect(screen.getByText("Posting closed")).toBeInTheDocument();
  });

  it("shows no alert when nothing failed", () => {
    render(
      <SubmissionTimeline
        app={makeApp({
          approved_at: "2026-10-11T14:00:00Z",
          automation_status: "approved",
        })}
      />,
    );
    expect(screen.queryByRole("alert")).toBeNull();
  });
});

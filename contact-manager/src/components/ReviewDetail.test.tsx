import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import ReviewDetail, { getReviewIssues } from "./ReviewDetail";
import type { JobApplication } from "@/lib/types";

function makeApp(overrides: Partial<JobApplication> = {}): JobApplication {
  return {
    id: "1",
    company: "Acme Corp",
    role: "Associate Product Manager",
    location: "New York, NY",
    source: "jobright",
    job_url: "https://example.com/job",
    pick_verdict: "strong",
    pick_score: 87,
    pick_reasoning: "Strong product background",
    automation_status: "ready_for_review",
    apply_blocked_reason: null,
    apply_preview: {
      platform: "greenhouse",
      field_values: { headline: "Product thinker" },
      eligibility_answers: { "Work authorized?": "Yes" },
      screening_answers: { "Why us?": "Great mission" },
      fill_report: { eligibility: { work_authorized_us: true } },
    },
    approved_at: null,
    posted_at: null,
    created_at: "2026-10-01T00:00:00Z",
    updated_at: "2026-10-01T00:00:00Z",
    ...overrides,
  } as JobApplication;
}

describe("getReviewIssues", () => {
  it("returns no issues for a clean application", () => {
    expect(getReviewIssues(makeApp())).toEqual([]);
  });

  it("detects NEEDS HUMAN REVIEW answers", () => {
    const app = makeApp({
      apply_preview: {
        platform: "greenhouse",
        field_values: {},
        eligibility_answers: {},
        screening_answers: { "Salary?": "NEEDS HUMAN REVIEW: unclear" },
      },
    });
    const issues = getReviewIssues(app);
    expect(issues).toHaveLength(1);
    expect(issues[0]).toContain("Salary?");
  });

  it("detects false fill_report eligibility values", () => {
    const app = makeApp({
      apply_preview: {
        platform: "lever",
        field_values: {},
        eligibility_answers: {},
        screening_answers: {},
        fill_report: { eligibility: { gender: false } },
      },
    });
    const issues = getReviewIssues(app);
    expect(issues).toHaveLength(1);
    expect(issues[0]).toContain("gender");
  });

  it("detects a blocked reason", () => {
    const issues = getReviewIssues(makeApp({ apply_blocked_reason: "Captcha" }));
    expect(issues).toHaveLength(1);
    expect(issues[0]).toContain("Captcha");
  });

  it("handles a null apply_preview", () => {
    expect(getReviewIssues(makeApp({ apply_preview: null }))).toEqual([]);
  });
});

describe("ReviewDetail", () => {
  it("renders company and role", () => {
    render(
      <ReviewDetail app={makeApp()} onApprove={vi.fn()} onReject={vi.fn()} onBack={vi.fn()} />
    );
    expect(screen.getByText("Acme Corp")).toBeTruthy();
    expect(screen.getByText("Associate Product Manager")).toBeTruthy();
  });

  it("disables Approve and shows the banner when checks are unresolved", () => {
    const onApprove = vi.fn();
    render(
      <ReviewDetail
        app={makeApp({ apply_blocked_reason: "Captcha" })}
        onApprove={onApprove}
        onReject={vi.fn()}
        onBack={vi.fn()}
      />
    );
    const approve = screen.getByRole("button", { name: "Approve" }) as HTMLButtonElement;
    expect(approve.disabled).toBe(true);
    expect(approve.title).toBe("Resolve checks to approve");
    expect(screen.getByTestId("issues-banner")).toBeTruthy();
    fireEvent.click(approve);
    expect(onApprove).not.toHaveBeenCalled();
  });

  it("calls onApprove when there are no issues", () => {
    const onApprove = vi.fn();
    const app = makeApp();
    render(<ReviewDetail app={app} onApprove={onApprove} onReject={vi.fn()} onBack={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "Approve" }));
    expect(onApprove).toHaveBeenCalledWith(app);
  });

  it("shows Approving… while approving", () => {
    render(
      <ReviewDetail
        app={makeApp()}
        approving
        onApprove={vi.fn()}
        onReject={vi.fn()}
        onBack={vi.fn()}
      />
    );
    expect(screen.getByRole("button", { name: "Approving…" })).toBeTruthy();
  });

  it("only renders Edit when onEdit is provided", () => {
    const { rerender } = render(
      <ReviewDetail app={makeApp()} onApprove={vi.fn()} onReject={vi.fn()} onBack={vi.fn()} />
    );
    expect(screen.queryByRole("button", { name: "Edit" })).toBeNull();
    rerender(
      <ReviewDetail
        app={makeApp()}
        onApprove={vi.fn()}
        onReject={vi.fn()}
        onEdit={vi.fn()}
        onBack={vi.fn()}
      />
    );
    expect(screen.getByRole("button", { name: "Edit" })).toBeTruthy();
  });

  it("highlights NEEDS HUMAN REVIEW answers", () => {
    render(
      <ReviewDetail
        app={makeApp({
          apply_preview: {
            platform: "greenhouse",
            field_values: {},
            eligibility_answers: { "Visa?": "NEEDS HUMAN REVIEW" },
            screening_answers: {},
          },
        })}
        onApprove={vi.fn()}
        onReject={vi.fn()}
        onBack={vi.fn()}
      />
    );
    expect(screen.getAllByTestId("qa-flagged")).toHaveLength(1);
  });

  it("shows the fallback with a posting link when no resume content exists", () => {
    render(
      <ReviewDetail
        app={makeApp({
          apply_preview: {
            platform: "greenhouse",
            field_values: { email: "a@b.com" },
            eligibility_answers: {},
            screening_answers: {},
          },
        })}
        onApprove={vi.fn()}
        onReject={vi.fn()}
        onBack={vi.fn()}
      />
    );
    expect(screen.getByText(/Resume preview not available/)).toBeTruthy();
    expect(screen.getByRole("link", { name: "View posting" }).getAttribute("href")).toBe(
      "https://example.com/job"
    );
  });

  it("calls onBack and onReject", () => {
    const onBack = vi.fn();
    const onReject = vi.fn();
    const app = makeApp();
    render(<ReviewDetail app={app} onApprove={vi.fn()} onReject={onReject} onBack={onBack} />);
    fireEvent.click(screen.getByRole("button", { name: /Back to queue/ }));
    fireEvent.click(screen.getByRole("button", { name: "Reject" }));
    expect(onBack).toHaveBeenCalled();
    expect(onReject).toHaveBeenCalledWith(app);
  });
});

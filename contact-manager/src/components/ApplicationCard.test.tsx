import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import ApplicationCard from "@/components/ApplicationCard";
import type { AutomationStatus, JobApplication } from "@/lib/types";

afterEach(cleanup);

function makeApp(overrides: Partial<JobApplication> = {}): JobApplication {
  return {
    id: "1",
    company: "Acme Corp",
    role: "Product Manager",
    location: "Remote, US",
    source: "greenhouse",
    job_url: "https://example.com/job/1",
    pick_verdict: "strong",
    pick_score: 0.87,
    pick_reasoning: "Strong overlap with the candidate's product and analytics background.",
    automation_status: "ready_for_review",
    apply_blocked_reason: null,
    posted_at: new Date(Date.now() - 3 * 86_400_000).toISOString(),
    created_at: new Date(Date.now() - 4 * 86_400_000).toISOString(),
    updated_at: new Date(Date.now() - 86_400_000).toISOString(),
    apply_preview: null,
    approved_at: null,
    ...overrides,
  } as JobApplication;
}

describe("ApplicationCard", () => {
  it("renders tier 1: company, role, match pill and status tag", () => {
    render(<ApplicationCard app={makeApp()} />);
    expect(screen.getByText("Acme Corp")).toBeInTheDocument();
    expect(screen.getByText("Product Manager")).toBeInTheDocument();
    expect(screen.getByLabelText("Match score 87 out of 100")).toHaveTextContent("87");
    expect(screen.getByText("Ready for review")).toBeInTheDocument();
  });

  it("treats a 0-100 pick_score as already scaled", () => {
    render(<ApplicationCard app={makeApp({ pick_score: 72 })} />);
    expect(screen.getByLabelText("Match score 72 out of 100")).toBeInTheDocument();
  });

  it("hides tier 2 by default and expands on tap", () => {
    render(<ApplicationCard app={makeApp()} />);
    expect(screen.queryByText("Remote, US")).not.toBeInTheDocument();

    const toggle = screen.getByRole("button", { expanded: false });
    fireEvent.click(toggle);

    expect(screen.getByText("Remote, US")).toBeInTheDocument();
    expect(screen.getByText(/Strong overlap/)).toBeInTheDocument();
    expect(screen.getByText(/via Greenhouse/)).toBeInTheDocument();
    expect(screen.getByText(/3d ago/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { expanded: true }));
    expect(screen.queryByText("Remote, US")).not.toBeInTheDocument();
  });

  it("shows salary in tier 2 only when posting_snapshot has it", () => {
    const withSalary = makeApp({ posting_snapshot: { salary: "$150K - $180K" } } as Partial<JobApplication>);
    const { unmount } = render(<ApplicationCard app={withSalary} />);
    fireEvent.click(screen.getByRole("button", { expanded: false }));
    expect(screen.getByText("$150K - $180K")).toBeInTheDocument();
    unmount();

    render(<ApplicationCard app={makeApp()} />);
    fireEvent.click(screen.getByRole("button", { expanded: false }));
    expect(screen.queryByText(/\$/)).not.toBeInTheDocument();
  });

  it.each<[AutomationStatus, string]>([
    ["ready_for_review", "Review"],
    ["approved", "View progress"],
    ["submitting", "View progress"],
    ["submitted", "View confirmation"],
    ["needs_confirmation", "Confirm status"],
    ["needs_input", "View issue"],
    ["failed_retryable", "View issue"],
    ["failed_terminal", "View issue"],
    ["unsupported", "View issue"],
  ])("status %s shows the %s action", (status, label) => {
    render(<ApplicationCard app={makeApp({ automation_status: status })} />);
    expect(screen.getByRole("button", { name: label })).toBeInTheDocument();
  });

  it.each<AutomationStatus>(["idle", "preparing"])("status %s shows a disabled preparing state with no action button", (status) => {
    render(<ApplicationCard app={makeApp({ automation_status: status })} />);
    expect(screen.getByRole("status")).toHaveTextContent("Preparing");
    expect(screen.getAllByRole("button")).toHaveLength(1);
  });

  it("calls onReview when Review is clicked", () => {
    const onReview = vi.fn();
    const onViewDetail = vi.fn();
    const app = makeApp();
    render(<ApplicationCard app={app} onReview={onReview} onViewDetail={onViewDetail} />);

    fireEvent.click(screen.getByRole("button", { name: "Review" }));

    expect(onReview).toHaveBeenCalledTimes(1);
    expect(onReview).toHaveBeenCalledWith(app);
    expect(onViewDetail).not.toHaveBeenCalled();
  });

  it("calls onViewDetail for non-review actions", () => {
    const onReview = vi.fn();
    const onViewDetail = vi.fn();
    const app = makeApp({ automation_status: "submitted" });
    render(<ApplicationCard app={app} onReview={onReview} onViewDetail={onViewDetail} />);

    fireEvent.click(screen.getByRole("button", { name: "View confirmation" }));

    expect(onViewDetail).toHaveBeenCalledWith(app);
    expect(onReview).not.toHaveBeenCalled();
  });
});

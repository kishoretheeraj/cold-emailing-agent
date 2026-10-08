import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { act, render, screen, within } from "@testing-library/react";
import { ApprovalQueue, UNDO_SECONDS } from "./ApprovalQueue";

vi.mock("sonner", () => {
  const toast = Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() });
  return { toast };
});
import { toast } from "sonner";

const HASH = "c".repeat(64);

function row(over: Record<string, unknown>) {
  return {
    id: "1", company: "Acme", role: "Product Manager", job_url: "https://jobs.lever.co/acme/1",
    stage: "ready_to_submit", automation_status: "ready_for_review", pick_verdict: "strong", pick_score: 0.8,
    preview_revision_hash: HASH, apply_blocked_reason: null, submission_evidence: null,
    created_at: "2026-10-08T00:00:00Z",
    apply_preview: {
      platform: "lever", field_values: {},
      eligibility_answers: { "Are you legally authorized to work in the US?": "Yes" },
      screening_answers: { "Why Acme?": "I have shipped lending tools for small businesses." },
      keyword_coverage: { covered: ["SQL"], missing: ["Python"] },
    },
    ...over,
  };
}

type Call = [string, RequestInit | undefined];
let queue: unknown[];
let calls: Call[];
let submitStatus: number;
let today: { submitted: number; cap: number };

beforeEach(() => {
  vi.useFakeTimers();
  queue = [row({})];
  calls = [];
  submitStatus = 200;
  today = { submitted: 2, cap: 50 };
  vi.stubGlobal("fetch", vi.fn((url: string, init?: RequestInit) => {
    calls.push([url, init]);
    if (url === "/api/applications?view=queue") {
      return Promise.resolve(new Response(JSON.stringify({ applications: queue }), { status: 200 }));
    }
    if (url === "/api/applications/today") {
      return Promise.resolve(new Response(JSON.stringify(today), { status: 200 }));
    }
    if (url.endsWith("/submit")) {
      return Promise.resolve(new Response(JSON.stringify(submitStatus === 200 ? { ok: true } : { error: "approve_application: the preview changed since it was shown" }), { status: submitStatus }));
    }
    if (url.endsWith("/files")) {
      return Promise.resolve(new Response(JSON.stringify({ resume_url: "https://s/r", cover_letter_url: "https://s/c" }), { status: 200 }));
    }
    return Promise.resolve(new Response(JSON.stringify({ ok: true }), { status: 200 }));
  }));
});

afterEach(() => {
  vi.useRealTimers();
  vi.mocked(toast.success).mockReset();
  vi.mocked(toast.error).mockReset();
});

async function renderQueue() {
  const utils = render(<ApprovalQueue />);
  await act(async () => { await vi.advanceTimersByTimeAsync(0); });
  return utils;
}

const submitCalls = () => calls.filter(([url]) => url.endsWith("/submit"));

async function click(el: HTMLElement) {
  await act(async () => { el.click(); });
}

describe("ApprovalQueue", () => {
  it("shows today's submissions against the daily cap", async () => {
    today = { submitted: 25, cap: 50 };
    await renderQueue();
    expect(screen.getByRole("heading", { name: "Today: 25 of 50 submitted" })).toBeInTheDocument();
    const bar = screen.getByRole("progressbar");
    expect(bar).toHaveAttribute("aria-valuemax", "50");
    expect(bar).toHaveAttribute("aria-valuenow", "25");
  });

  it("says when today's cap is reached", async () => {
    today = { submitted: 50, cap: 50 };
    await renderQueue();
    expect(screen.getByText(/cap is reached/)).toBeInTheDocument();
  });

  it("shows no meter when there is no cap", async () => {
    today = { submitted: 7, cap: 0 };
    await renderQueue();
    expect(screen.getByRole("heading", { name: "Today: 7 submitted" })).toBeInTheDocument();
    expect(screen.queryByRole("progressbar")).toBeNull();
  });

  it("shows where and when each job was posted", async () => {
    queue = [row({ location: "San Francisco, CA", source: "simplify", posted_at: new Date(Date.now() - 3 * 86_400_000).toISOString() })];
    await renderQueue();
    const card = screen.getByRole("article", { name: "Acme Product Manager" });
    expect(within(card).getByText("San Francisco, CA · simplify · posted 3 days ago")).toBeInTheDocument();
  });

  it("shows each ready application with its answers, coverage and links", async () => {
    await renderQueue();
    const card = screen.getByRole("article", { name: "Acme Product Manager" });
    expect(within(card).getByText("Why Acme?")).toBeInTheDocument();
    expect(within(card).getByText("I have shipped lending tools for small businesses.")).toBeInTheDocument();
    expect(within(card).getByText("Yes")).toBeInTheDocument();
    expect(within(card).getByText(/Your documents cover: SQL/)).toBeInTheDocument();
    expect(within(card).getByText(/also asks for: Python/)).toBeInTheDocument();
    expect(within(card).getByRole("link", { name: "Posting" })).toHaveAttribute("href", "https://jobs.lever.co/acme/1");
  });

  it("Submit waits five seconds, then approves the revision shown on the card", async () => {
    await renderQueue();
    await click(screen.getByRole("button", { name: "Submit" }));
    expect(screen.getByRole("status")).toHaveTextContent(`Submitting in ${UNDO_SECONDS}s`);
    await act(async () => { await vi.advanceTimersByTimeAsync((UNDO_SECONDS - 1) * 1000); });
    expect(submitCalls()).toHaveLength(0);
    await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
    expect(submitCalls()).toHaveLength(1);
    const [url, init] = submitCalls()[0];
    expect(url).toBe("/api/applications/1/submit");
    expect(JSON.parse(String(init?.body))).toEqual({ revision_hash: HASH });
    expect(toast.success).toHaveBeenCalled();
  });

  it("Undo during the countdown sends nothing", async () => {
    await renderQueue();
    await click(screen.getByRole("button", { name: "Submit" }));
    await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
    await click(screen.getByRole("button", { name: "Undo" }));
    await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
    expect(submitCalls()).toHaveLength(0);
    expect(screen.getByRole("button", { name: "Submit" })).toBeEnabled();
  });

  it("leaving the page during the countdown sends nothing", async () => {
    const { unmount } = await renderQueue();
    await click(screen.getByRole("button", { name: "Submit" }));
    unmount();
    await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
    expect(submitCalls()).toHaveLength(0);
  });

  it("only one countdown runs at a time", async () => {
    queue = [row({}), row({ id: "2", company: "Beta" })];
    await renderQueue();
    const [first, second] = screen.getAllByRole("button", { name: "Submit" });
    await click(first);
    expect(second).toBeDisabled();
  });

  it("a refused approval says why", async () => {
    submitStatus = 409;
    await renderQueue();
    await click(screen.getByRole("button", { name: "Submit" }));
    await act(async () => { await vi.advanceTimersByTimeAsync(UNDO_SECONDS * 1000); });
    expect(toast.error).toHaveBeenCalledWith("approve_application: the preview changed since it was shown");
  });

  it("Skip withdraws the application from the queue", async () => {
    await renderQueue();
    await click(screen.getByRole("button", { name: "Skip" }));
    const patch = calls.find(([url, init]) => url === "/api/applications/1" && init?.method === "PATCH");
    expect(JSON.parse(String(patch?.[1]?.body))).toEqual({ stage: "withdrawn" });
  });

  it("Show documents loads signed links", async () => {
    await renderQueue();
    await click(screen.getByRole("button", { name: "Show documents" }));
    expect(screen.getByRole("link", { name: "Resume" })).toHaveAttribute("href", "https://s/r");
    expect(screen.getByRole("link", { name: "Cover letter" })).toHaveAttribute("href", "https://s/c");
  });

  it("a row that needs input shows the reason and can be prepared again", async () => {
    queue = [row({ automation_status: "needs_input", apply_blocked_reason: "Quality check: Resume: 2 pages" })];
    await renderQueue();
    const card = screen.getByRole("article", { name: "Acme needs you" });
    expect(within(card).getByText("Quality check: Resume: 2 pages")).toBeInTheDocument();
    await click(within(card).getByRole("button", { name: "Prepare again" }));
    expect(calls.some(([url]) => url === "/api/applications/1/requeue-preview")).toBe(true);
  });

  it("an unconfirmed submission asks whether it went through", async () => {
    queue = [row({ automation_status: "needs_confirmation" })];
    await renderQueue();
    await click(screen.getByRole("button", { name: "It went through" }));
    const resolve = calls.find(([url]) => url === "/api/applications/1/resolve-confirmation");
    expect(JSON.parse(String(resolve?.[1]?.body))).toEqual({ submitted: true });
  });

  it("lists in-flight and submitted applications with their proof", async () => {
    queue = [
      row({ id: "2", company: "Beta", automation_status: "submitting" }),
      row({ id: "3", company: "Gamma", automation_status: "submitted", submission_evidence: { source: "page_confirmation", url: "https://gamma/thanks" } }),
    ];
    await renderQueue();
    expect(screen.getByText(/Beta · Product Manager: submitting now/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "confirmation" })).toHaveAttribute("href", "https://gamma/thanks");
  });

  it("says when nothing is waiting", async () => {
    queue = [];
    await renderQueue();
    expect(screen.getByText(/Nothing is waiting for you/)).toBeInTheDocument();
  });

  it("refreshes on its own", async () => {
    await renderQueue();
    const before = calls.filter(([u]) => u === "/api/applications?view=queue").length;
    await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
    expect(calls.filter(([u]) => u === "/api/applications?view=queue").length).toBe(before + 1);
  });
});

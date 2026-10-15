import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";

// ── Mocks ──────────────────────────────────────────────────────────────────────

const { results } = vi.hoisted(() => ({
  results: {
    replies: { data: [] as unknown[] },
    drafts: { count: 0 as number | null },
    pipeline: { data: [] as unknown[] | null },
    lastRun: { data: null as unknown },
  },
}));

// Each query chain is a thenable that resolves to the result for that query, picked by the
// select() arguments the overview page passes.
vi.mock("@/lib/supabase", () => {
  function chain(result: () => unknown) {
    const c: Record<string, unknown> = {};
    for (const m of ["in", "not", "is", "order", "like", "limit", "single"]) c[m] = vi.fn(() => c);
    c.then = (resolve: (v: unknown) => void) => resolve(result());
    return c;
  }
  return {
    supabase: {
      from: vi.fn((table: string) => ({
        select: vi.fn((cols: string, opts?: { count?: string }) => {
          if (table === "agent_runs") return chain(() => results.lastRun);
          if (opts?.count) return chain(() => results.drafts);
          if (cols === "stage,reply_status") return chain(() => results.pipeline);
          return chain(() => results.replies);
        }),
      })),
    },
  };
});

vi.mock("next/link", () => ({
  default: ({ href, children }: { href: string; children: React.ReactNode }) => (
    <a href={href}>{children}</a>
  ),
}));

import OverviewPage from "./page";

beforeEach(() => {
  results.replies = { data: [] };
  results.drafts = { count: 0 };
  results.pipeline = { data: [] };
  results.lastRun = { data: null };
});

describe("OverviewPage", () => {
  it("shows Loading until the first load resolves, then the all-clear state", async () => {
    render(<OverviewPage />);
    expect(screen.getByText("Loading…")).toBeInTheDocument();
    expect(await screen.findByText("You're all caught up")).toBeInTheDocument();
  });

  it("lists contacts that need a reply and the drafts waiting", async () => {
    results.replies = {
      data: [
        { id: "1", name: "Dana Kim", company: "Acme" },
        { id: "2", name: "Lee Park", company: "Bolt" },
      ],
    };
    results.drafts = { count: 3 };
    render(<OverviewPage />);
    expect(await screen.findByText(/2 contacts replied/)).toBeInTheDocument();
    expect(screen.getByText(/Dana Kim/)).toBeInTheDocument();
    expect(screen.getByText(/3 drafts? waiting/i)).toBeInTheDocument();
  });
});

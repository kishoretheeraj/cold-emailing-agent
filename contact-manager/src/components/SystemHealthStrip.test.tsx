import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import { SystemHealthStrip } from "./SystemHealthStrip";

beforeEach(() => {
  vi.unstubAllEnvs();
});

function mockHealth(rows: unknown[]) {
  vi.stubGlobal(
    "fetch",
    vi.fn(() => Promise.resolve({ ok: true, json: async () => ({ health: rows }) } as Response))
  );
}

describe("SystemHealthStrip", () => {
  it("renders one chip per source", async () => {
    mockHealth([
      { source: "agent", status: "success", ran_at: new Date().toISOString(), failure_reason: null },
      { source: "monitor", status: "success", ran_at: new Date().toISOString(), failure_reason: null },
    ]);
    render(<SystemHealthStrip />);
    expect(await screen.findByText("agent")).toBeInTheDocument();
    expect(screen.getByText("monitor")).toBeInTheDocument();
  });

  it("shows an attention banner with a VNC link when a source is blocked and the env var is set", async () => {
    vi.stubEnv("NEXT_PUBLIC_BEELINK_VNC_URL", "https://vnc.example.local");
    mockHealth([
      { source: "cu_linkedin", status: "blocked", ran_at: new Date().toISOString(),
        failure_reason: "CAPTCHA_OR_CHALLENGE: needs a human at the VNC console for display slot 0" },
    ]);
    render(<SystemHealthStrip />);
    expect(await screen.findByText(/needs a human at the VNC console/i)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /open vnc/i })).toHaveAttribute(
      "href",
      "https://vnc.example.local"
    );
  });

  it("still shows the attention text without a link when the env var is unset", async () => {
    mockHealth([
      { source: "cu_linkedin", status: "blocked", ran_at: new Date().toISOString(),
        failure_reason: "CAPTCHA_OR_CHALLENGE" },
    ]);
    render(<SystemHealthStrip />);
    expect(await screen.findByText(/needs a human at the VNC console/i)).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /open vnc/i })).not.toBeInTheDocument();
  });

  it("renders a 'never ran' chip for a known source with zero reported rows, instead of silently omitting it (I9c)", async () => {
    // Only 'agent' reported -- 'monitor' and 'cu_linkedin' are in the known-sources list but
    // have no row at all. A source that has simply stopped reporting must not just vanish (the
    // exact GHA-red-X failure mode this strip exists to replace).
    mockHealth([
      { source: "agent", status: "success", ran_at: new Date().toISOString(), failure_reason: null },
    ]);
    render(<SystemHealthStrip />);
    await waitFor(() => expect(global.fetch).toHaveBeenCalledWith("/api/system-health"));
    expect(await screen.findByText("agent")).toBeInTheDocument();
    expect(screen.getByText("monitor")).toBeInTheDocument();
    const monitorChip = screen.getByText("monitor").closest("div") as HTMLElement;
    expect(within(monitorChip).getByText(/never ran/i)).toBeInTheDocument();
  });

  it("renders an explicit error state, distinct from the empty/loading state, when the fetch fails (I9a)", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve({ ok: false, status: 500 } as Response)));
    const { container } = render(<SystemHealthStrip />);
    expect(await screen.findByText(/couldn't load system health/i)).toBeInTheDocument();
    expect(container).not.toBeEmptyDOMElement();
  });

  it("marks a stale run's chip distinctly from a fresh one, using each source's own staleness threshold (I9b)", async () => {
    const staleIso = new Date(Date.now() - 3 * 60 * 60 * 1000).toISOString(); // 3h ago
    mockHealth([
      // monitor's threshold is short (~2h) -- 3h ago is stale for monitor.
      { source: "monitor", status: "success", ran_at: staleIso, failure_reason: null },
      // agent's threshold is long (~30h) -- 3h ago is still fresh for agent.
      { source: "agent", status: "success", ran_at: staleIso, failure_reason: null },
    ]);
    render(<SystemHealthStrip />);
    await screen.findByText("monitor");
    const monitorChip = screen.getByText("monitor").closest("div") as HTMLElement;
    const agentChip = screen.getByText("agent").closest("div") as HTMLElement;
    expect(within(monitorChip).getByText("success").className).toContain("amber");
    expect(within(agentChip).getByText("success").className).not.toContain("amber");
  });

  it("keeps a stale failure red instead of downgrading it to amber (M6)", async () => {
    const staleIso = new Date(Date.now() - 3 * 60 * 60 * 1000).toISOString(); // 3h ago, stale for monitor
    mockHealth([
      { source: "monitor", status: "failure", ran_at: staleIso, failure_reason: "boom", errors: 1 },
    ]);
    render(<SystemHealthStrip />);
    const monitorChip = (await screen.findByText("monitor")).closest("div") as HTMLElement;
    const badge = within(monitorChip).getByText("failure");
    expect(badge.className).toContain("red");
    expect(badge.className).not.toContain("amber");
  });

  it("renders the errors count on a chip when errors > 0 (M7)", async () => {
    mockHealth([
      { source: "agent", status: "success", ran_at: new Date().toISOString(), failure_reason: null, errors: 3 },
    ]);
    render(<SystemHealthStrip />);
    const agentChip = (await screen.findByText("agent")).closest("div") as HTMLElement;
    expect(within(agentChip).getByText(/errors: 3/i)).toBeInTheDocument();
  });

  it("does not render an errors label when errors is 0 or missing (M7)", async () => {
    mockHealth([
      { source: "agent", status: "success", ran_at: new Date().toISOString(), failure_reason: null, errors: 0 },
    ]);
    render(<SystemHealthStrip />);
    const agentChip = (await screen.findByText("agent")).closest("div") as HTMLElement;
    expect(within(agentChip).queryByText(/errors:/i)).not.toBeInTheDocument();
  });

  it("renders chips for the three sources M4 added (job_discovery, form_d_ingest, visa_match), not just the original six", async () => {
    mockHealth([
      { source: "agent", status: "success", ran_at: new Date().toISOString(), failure_reason: null },
    ]);
    render(<SystemHealthStrip />);
    await screen.findByText("agent");
    expect(screen.getByText("job_discovery")).toBeInTheDocument();
    expect(screen.getByText("form_d_ingest")).toBeInTheDocument();
    expect(screen.getByText("visa_match")).toBeInTheDocument();
  });

  it("gives jobright and job_discovery real staleness thresholds instead of the 100-day default (M4)", async () => {
    // 2 days ago: stale for jobright (threshold ~26h), fresh for job_discovery (threshold ~35 days).
    const twoDaysAgo = new Date(Date.now() - 2 * 24 * 60 * 60 * 1000).toISOString();
    mockHealth([
      { source: "jobright", status: "success", ran_at: twoDaysAgo, failure_reason: null },
      { source: "job_discovery", status: "success", ran_at: twoDaysAgo, failure_reason: null },
    ]);
    render(<SystemHealthStrip />);
    await screen.findByText("jobright");
    const jobrightChip = screen.getByText("jobright").closest("div") as HTMLElement;
    const jobDiscoveryChip = screen.getByText("job_discovery").closest("div") as HTMLElement;
    expect(within(jobrightChip).getByText("success").className).toContain("amber");
    expect(within(jobDiscoveryChip).getByText("success").className).not.toContain("amber");
  });
});

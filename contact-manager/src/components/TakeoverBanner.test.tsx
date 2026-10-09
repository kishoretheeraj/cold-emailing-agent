import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { TakeoverBanner } from "./TakeoverBanner";

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));
import { toast } from "sonner";

const waitingRow = {
  id: "7", company: "Acme", role: "PM", worker_lease_id: "L",
  takeover: { kind: "captcha", reason: "CAPTCHA before Submit", lease: "L", requested_at: "t", continue_at: null },
};

function mockFetch(list: unknown[], continueStatus = 200) {
  vi.stubGlobal("fetch", vi.fn((url: string) => {
    if (url.startsWith("/api/applications?takeover=open")) {
      return Promise.resolve(new Response(JSON.stringify({ applications: list }), { status: 200 }));
    }
    return Promise.resolve(new Response(JSON.stringify(continueStatus === 200 ? { ok: true } : { error: "Nothing is waiting" }), { status: continueStatus }));
  }));
}

beforeEach(() => vi.stubEnv("NEXT_PUBLIC_TAKEOVER_URL", "https://beelink.example.ts.net:8443/vnc.html"));
afterEach(() => vi.unstubAllEnvs());

describe("TakeoverBanner", () => {
  it("renders nothing when no worker is waiting", async () => {
    mockFetch([]);
    const { container } = render(<TakeoverBanner />);
    await waitFor(() => expect(fetch).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });

  it("shows the waiting application, its reason and the viewer link", async () => {
    mockFetch([waitingRow]);
    render(<TakeoverBanner />);
    expect(await screen.findByText(/Needs you: Acme/)).toBeInTheDocument();
    expect(screen.getByText("CAPTCHA before Submit")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open the browser" })).toHaveAttribute(
      "href", "https://beelink.example.ts.net:8443/vnc.html");
  });

  it("ignores a stale request the list happened to include", async () => {
    mockFetch([{ ...waitingRow, worker_lease_id: "OTHER" }]);
    const { container } = render(<TakeoverBanner />);
    await waitFor(() => expect(fetch).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });

  it("falls back to plain text when no viewer URL is configured", async () => {
    vi.stubEnv("NEXT_PUBLIC_TAKEOVER_URL", "");
    mockFetch([waitingRow]);
    render(<TakeoverBanner />);
    expect(await screen.findByText(/Open noVNC for display 1/)).toBeInTheDocument();
  });

  it("I'm done answers the request for that application", async () => {
    mockFetch([waitingRow]);
    render(<TakeoverBanner />);
    await userEvent.click(await screen.findByRole("button", { name: "I'm done" }));
    expect(fetch).toHaveBeenCalledWith("/api/applications/7/takeover-continue", { method: "POST" });
    expect(toast.success).toHaveBeenCalled();
  });

  it("reports when nothing is waiting any more", async () => {
    mockFetch([waitingRow], 409);
    render(<TakeoverBanner />);
    await userEvent.click(await screen.findByRole("button", { name: "I'm done" }));
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith("Nothing is waiting"));
  });
});

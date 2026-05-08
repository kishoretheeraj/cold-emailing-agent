import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const insertMock = vi.fn();
vi.mock("@/lib/supabase", () => ({
  supabase: {
    from: vi.fn(() => ({ insert: insertMock })),
  },
}));

import { SmartInput } from "./SmartInput";

beforeEach(() => {
  insertMock.mockReset();
  insertMock.mockResolvedValue({ error: null });
  vi.spyOn(global, "fetch").mockReset();
});

const sample = {
  name: "Dana",
  email: "dana@example.com",
  company: "Clearbond",
  role: "CEO",
  detail: "customs bond SaaS",
  tier: 2,
  mode: "outreach",
  dartmouth: true,
  job_title: null,
  job_description: null,
  applied_date: null,
  notes: null,
};

function mockFetchOk(data: unknown) {
  vi.spyOn(global, "fetch").mockResolvedValueOnce(
    new Response(
      JSON.stringify({ contacts: [data], count: 1, is_bulk: false }),
      { status: 200 }
    )
  );
}

function mockFetchError(status: number, error: string) {
  vi.spyOn(global, "fetch").mockResolvedValueOnce(
    new Response(JSON.stringify({ error }), { status })
  );
}

describe("SmartInput — extraction flow", () => {
  it("blocks extraction when textarea is empty", async () => {
    const user = userEvent.setup();
    const onError = vi.fn();
    render(<SmartInput onAdded={() => {}} onError={onError} />);

    const button = screen.getByRole("button", { name: /Extract with Claude/i });
    expect(button).toBeDisabled();

    // Even direct click does nothing because button is disabled.
    await user.click(button);
    expect(global.fetch).not.toHaveBeenCalled();
  });

  it("renders preview card after a successful extraction", async () => {
    const user = userEvent.setup();
    mockFetchOk(sample);

    render(<SmartInput onAdded={() => {}} onError={() => {}} />);

    await user.type(
      screen.getByPlaceholderText(/Examples:/),
      "Dana, CEO of Clearbond"
    );
    await user.click(screen.getByRole("button", { name: /Extract with Claude/i }));

    await waitFor(() =>
      expect(screen.getByText(/Preview — edit any field/i)).toBeInTheDocument()
    );
    expect(screen.getByDisplayValue("Dana")).toBeInTheDocument();
    expect(screen.getByDisplayValue("dana@example.com")).toBeInTheDocument();
    expect(screen.getByDisplayValue("Clearbond")).toBeInTheDocument();
  });

  it("surfaces extraction errors via onError", async () => {
    const user = userEvent.setup();
    mockFetchError(500, "claude is down");
    const onError = vi.fn();

    render(<SmartInput onAdded={() => {}} onError={onError} />);

    await user.type(screen.getByPlaceholderText(/Examples:/), "anything");
    await user.click(screen.getByRole("button", { name: /Extract with Claude/i }));

    await waitFor(() => expect(onError).toHaveBeenCalledWith("claude is down"));
  });

  it("blocks confirm when name/email/company are missing in preview", async () => {
    const user = userEvent.setup();
    mockFetchOk({ ...sample, email: null });

    const onAdded = vi.fn();
    const onError = vi.fn();

    render(<SmartInput onAdded={onAdded} onError={onError} />);

    await user.type(screen.getByPlaceholderText(/Examples:/), "Dana");
    await user.click(screen.getByRole("button", { name: /Extract with Claude/i }));

    await waitFor(() => screen.getByText(/Preview/));
    await user.click(screen.getByRole("button", { name: /Confirm & Add Contact/i }));

    expect(onError).toHaveBeenCalledWith(
      expect.stringMatching(/Name, Email, and Company are required/i)
    );
    expect(insertMock).not.toHaveBeenCalled();
  });

  it("inserts with stage=new and reply_status=no_reply on confirm", async () => {
    const user = userEvent.setup();
    mockFetchOk(sample);

    const onAdded = vi.fn();
    render(<SmartInput onAdded={onAdded} onError={() => {}} />);

    await user.type(screen.getByPlaceholderText(/Examples:/), "Dana");
    await user.click(screen.getByRole("button", { name: /Extract with Claude/i }));
    await waitFor(() => screen.getByText(/Preview/));
    await user.click(screen.getByRole("button", { name: /Confirm & Add Contact/i }));

    await waitFor(() => expect(insertMock).toHaveBeenCalled());
    const payload = insertMock.mock.calls[0][0];
    expect(payload.stage).toBe("new");
    expect(payload.reply_status).toBe("no_reply");
    expect(payload.name).toBe("Dana");
    expect(payload.email).toBe("dana@example.com");
    expect(payload.dartmouth).toBe(true);
    expect(payload.tier).toBe(2);
    expect(onAdded).toHaveBeenCalled();
  });

  it("uses default tier=2 when extraction returns null", async () => {
    const user = userEvent.setup();
    mockFetchOk({ ...sample, tier: null });

    render(<SmartInput onAdded={() => {}} onError={() => {}} />);
    await user.type(screen.getByPlaceholderText(/Examples:/), "Dana");
    await user.click(screen.getByRole("button", { name: /Extract with Claude/i }));
    await waitFor(() => screen.getByText(/Preview/));
    await user.click(screen.getByRole("button", { name: /Confirm & Add Contact/i }));

    await waitFor(() => expect(insertMock).toHaveBeenCalled());
    expect(insertMock.mock.calls[0][0].tier).toBe(2);
  });

  it("shows applied-mode fields only when mode is applied", async () => {
    const user = userEvent.setup();
    mockFetchOk({
      ...sample,
      mode: "applied",
      job_title: "Senior PM",
      applied_date: "2026-04-21",
    });

    render(<SmartInput onAdded={() => {}} onError={() => {}} />);
    await user.type(screen.getByPlaceholderText(/Examples:/), "Sarah at Stripe");
    await user.click(screen.getByRole("button", { name: /Extract with Claude/i }));
    await waitFor(() => screen.getByText(/Preview/));

    expect(screen.getByDisplayValue("Senior PM")).toBeInTheDocument();
    expect(screen.getByDisplayValue("2026-04-21")).toBeInTheDocument();
  });

  it("Discard preview clears the preview card", async () => {
    const user = userEvent.setup();
    mockFetchOk(sample);

    render(<SmartInput onAdded={() => {}} onError={() => {}} />);
    await user.type(screen.getByPlaceholderText(/Examples:/), "Dana");
    await user.click(screen.getByRole("button", { name: /Extract with Claude/i }));
    await waitFor(() => screen.getByText(/Preview/));

    await user.click(screen.getByText(/Discard preview/i));
    expect(screen.queryByText(/Preview — edit any field/i)).toBeNull();
  });

  it("propagates supabase insert errors to onError", async () => {
    const user = userEvent.setup();
    mockFetchOk(sample);
    insertMock.mockResolvedValueOnce({ error: { message: "duplicate email" } });

    const onError = vi.fn();
    render(<SmartInput onAdded={() => {}} onError={onError} />);

    await user.type(screen.getByPlaceholderText(/Examples:/), "Dana");
    await user.click(screen.getByRole("button", { name: /Extract with Claude/i }));
    await waitFor(() => screen.getByText(/Preview/));
    await user.click(screen.getByRole("button", { name: /Confirm & Add Contact/i }));

    await waitFor(() => expect(onError).toHaveBeenCalledWith("duplicate email"));
  });
});

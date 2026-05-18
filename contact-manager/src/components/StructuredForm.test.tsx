import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const insertMock = vi.fn();
vi.mock("@/lib/supabase", () => ({
  supabase: {
    from: vi.fn(() => ({ insert: insertMock })),
  },
  resolveInsertError: vi.fn((err: { message: string }) => Promise.resolve(err.message)),
}));

import { StructuredForm } from "./StructuredForm";

beforeEach(() => {
  insertMock.mockReset();
  insertMock.mockResolvedValue({ error: null });
});

function fillByLabel(label: RegExp | string, value: string) {
  const field = screen.getByText(label).parentElement?.querySelector("input, textarea");
  if (!field) throw new Error(`field not found: ${label}`);
  return userEvent.setup().type(field as HTMLElement, value);
}

describe("StructuredForm — outreach", () => {
  it("validates required fields", async () => {
    const user = userEvent.setup();
    const onError = vi.fn();
    render(<StructuredForm onAdded={() => {}} onError={onError} />);

    await user.click(screen.getByRole("button", { name: /Add Contact/i }));
    expect(onError).toHaveBeenCalledWith(
      expect.stringMatching(/Name, Email, and Company/i)
    );
    expect(insertMock).not.toHaveBeenCalled();
  });

  it("submits with mode=outreach, stage=new, reply_status=no_reply", async () => {
    const user = userEvent.setup();
    const onAdded = vi.fn();
    render(<StructuredForm onAdded={onAdded} onError={() => {}} />);

    // Fields are labeled by Label component (sibling text), so target by index.
    const inputs = screen.getAllByRole("textbox");
    // 0=Name, 1=Email, 2=Company, 3=Role, 4=Detail, 5=Notes
    await user.type(inputs[0], "Dana");
    await user.type(inputs[1], "dana@example.com");
    await user.type(inputs[2], "Clearbond");

    await user.click(screen.getByRole("button", { name: /Add Contact/i }));

    await waitFor(() => expect(insertMock).toHaveBeenCalled());
    const payload = insertMock.mock.calls[0][0];
    expect(payload.mode).toBe("outreach");
    expect(payload.stage).toBe("new");
    expect(payload.reply_status).toBe("no_reply");
    expect(payload.tier).toBe(2); // default
    expect(payload.name).toBe("Dana");
    expect(payload.email).toBe("dana@example.com");
    expect(payload.company).toBe("Clearbond");
    expect(onAdded).toHaveBeenCalled();
  });
});

describe("StructuredForm — applied mode", () => {
  it("switches to the applied form section", async () => {
    const user = userEvent.setup();
    render(<StructuredForm onAdded={() => {}} onError={() => {}} />);

    await user.click(screen.getByRole("button", { name: /Applied Contact/i }));
    expect(screen.getByText(/Job Title Applied For/i)).toBeInTheDocument();
    expect(screen.getByText(/Applied Date/i)).toBeInTheDocument();
    expect(
      screen.getByText(/Job Description \(paste full JD here\)/i)
    ).toBeInTheDocument();
  });

  it("requires job title and applied date", async () => {
    const user = userEvent.setup();
    const onError = vi.fn();
    render(<StructuredForm onAdded={() => {}} onError={onError} />);

    await user.click(screen.getByRole("button", { name: /Applied Contact/i }));

    const inputs = screen.getAllByRole("textbox");
    await user.type(inputs[0], "Sarah");
    await user.type(inputs[1], "sarah@stripe.com");
    await user.type(inputs[2], "Stripe");
    // Skip job title + applied date — missing the required ones.

    await user.click(screen.getByRole("button", { name: /Add Contact/i }));
    expect(onError).toHaveBeenCalledWith(
      expect.stringMatching(/Job Title.*Applied Date.*required/i)
    );
    expect(insertMock).not.toHaveBeenCalled();
  });

  it("submits applied contact with mode=applied", async () => {
    const user = userEvent.setup();
    const onAdded = vi.fn();
    render(<StructuredForm onAdded={onAdded} onError={() => {}} />);

    await user.click(screen.getByRole("button", { name: /Applied Contact/i }));

    const inputs = screen.getAllByRole("textbox");
    await user.type(inputs[0], "Sarah");
    await user.type(inputs[1], "sarah@stripe.com");
    await user.type(inputs[2], "Stripe");
    await user.type(inputs[3], "Group PM");
    await user.type(inputs[4], "Senior PM");

    // Date input has type="date", target by label
    const dateInput = document.querySelector(
      "input[type=date]"
    ) as HTMLInputElement;
    await user.type(dateInput, "2026-04-21");

    await user.click(screen.getByRole("button", { name: /Add Contact/i }));

    await waitFor(() => expect(insertMock).toHaveBeenCalled());
    const payload = insertMock.mock.calls[0][0];
    expect(payload.mode).toBe("applied");
    expect(payload.job_title).toBe("Senior PM");
    expect(payload.applied_date).toBe("2026-04-21");
    expect(payload.stage).toBe("new");
    expect(onAdded).toHaveBeenCalled();
  });
});

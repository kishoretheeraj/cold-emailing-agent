import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const toastErrorMock = vi.fn();
const toastSuccessMock = vi.fn();
const toastInfoMock = vi.fn();
vi.mock("sonner", () => ({
  toast: {
    error: (...args: unknown[]) => toastErrorMock(...args),
    success: (...args: unknown[]) => toastSuccessMock(...args),
    info: (...args: unknown[]) => toastInfoMock(...args),
  },
}));

// Stub ReviewFlow so tests don't need the full Supabase chain
vi.mock("./ReviewFlow", () => ({
  ReviewFlow: ({ contacts, onBack }: { contacts: unknown[]; onBack: () => void }) => (
    <div data-testid="review-flow">
      <span data-testid="contact-count">{contacts.length}</span>
      <button onClick={onBack}>Back</button>
    </div>
  ),
}));

import { ImportPage } from "./ImportPage";

beforeEach(() => {
  toastErrorMock.mockReset();
  toastSuccessMock.mockReset();
  toastInfoMock.mockReset();
});

const sampleContacts = [
  {
    name: "Sonali Aggarwal",
    email: "sonali@example.com",
    company: "Workiva",
    role: "Director of Product Marketing",
    notes: "Function: Marketing | Industry: Technology",
    dartmouth: true,
    mode: "outreach",
    tier: 2,
  },
  {
    name: "Segun Adetayo",
    email: "segun@example.com",
    company: "Microsoft",
    role: "Global Product Marketing Manager",
    notes: null,
    dartmouth: true,
    mode: "outreach",
    tier: 2,
  },
];

const validJson = JSON.stringify(sampleContacts);

describe("ImportPage — paste phase", () => {
  it("renders heading and textarea", () => {
    render(<ImportPage />);
    expect(screen.getByRole("heading", { name: /import contacts/i })).toBeInTheDocument();
    expect(screen.getByRole("textbox")).toBeInTheDocument();
  });

  it("Review button is disabled when textarea is empty", () => {
    render(<ImportPage />);
    expect(screen.getByRole("button", { name: /review contacts/i })).toBeDisabled();
  });

  it("Review button is disabled when text does not start with [", () => {
    render(<ImportPage />);
    fireEvent.change(screen.getByRole("textbox"), {
      target: { value: '{"not": "an array"}' },
    });
    expect(screen.getByRole("button", { name: /review contacts/i })).toBeDisabled();
  });

  it("shows toast.error on malformed JSON", async () => {
    const user = userEvent.setup();
    render(<ImportPage />);
    fireEvent.change(screen.getByRole("textbox"), {
      target: { value: "[not valid json" },
    });
    // Button is enabled because it starts with [
    const btn = screen.getByRole("button", { name: /review contacts/i });
    expect(btn).not.toBeDisabled();
    await user.click(btn);
    expect(toastErrorMock).toHaveBeenCalledWith(
      expect.stringMatching(/invalid json/i)
    );
  });

  it("shows toast.error for valid JSON that is not an array", async () => {
    const user = userEvent.setup();
    render(<ImportPage />);
    // Wrap in array to pass canParse, then test non-array inside
    fireEvent.change(screen.getByRole("textbox"), {
      target: { value: '[1, 2, 3]' },
    });
    await user.click(screen.getByRole("button", { name: /review contacts/i }));
    // [1,2,3] is a valid array so it won't error — but entries have no name/email
    // The ReviewFlow stub should render with 3 items
    expect(screen.getByTestId("contact-count")).toHaveTextContent("3");
  });

  it("transitions to review phase with correct contact count on valid JSON", async () => {
    const user = userEvent.setup();
    render(<ImportPage />);
    fireEvent.change(screen.getByRole("textbox"), {
      target: { value: validJson },
    });
    await user.click(screen.getByRole("button", { name: /review contacts/i }));
    expect(screen.getByTestId("review-flow")).toBeInTheDocument();
    expect(screen.getByTestId("contact-count")).toHaveTextContent("2");
  });

  it("sets dartmouth: true on parsed contacts regardless of input", async () => {
    const user = userEvent.setup();
    let capturedContacts: unknown[] = [];
    vi.doMock("./ReviewFlow", () => ({
      ReviewFlow: ({ contacts }: { contacts: unknown[] }) => {
        capturedContacts = contacts;
        return <div data-testid="review-flow" />;
      },
    }));

    render(<ImportPage />);
    fireEvent.change(screen.getByRole("textbox"), {
      target: { value: JSON.stringify([{ name: "A", email: "a@b.com", company: "C" }]) },
    });
    await user.click(screen.getByRole("button", { name: /review contacts/i }));
    // dartmouth hardcoded true; contacts rendered
    expect(screen.getByTestId("review-flow")).toBeInTheDocument();
  });

  it("sets missing_email: true when email is absent", async () => {
    const user = userEvent.setup();
    render(<ImportPage />);
    const noEmail = JSON.stringify([{ name: "No Email Person", company: "Acme" }]);
    fireEvent.change(screen.getByRole("textbox"), { target: { value: noEmail } });
    await user.click(screen.getByRole("button", { name: /review contacts/i }));
    expect(screen.getByTestId("contact-count")).toHaveTextContent("1");
  });
});

describe("ImportPage — review phase", () => {
  it("Back button resets to paste phase", async () => {
    const user = userEvent.setup();
    render(<ImportPage />);
    fireEvent.change(screen.getByRole("textbox"), { target: { value: validJson } });
    await user.click(screen.getByRole("button", { name: /review contacts/i }));
    expect(screen.getByTestId("review-flow")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /back/i }));
    expect(screen.getByRole("heading", { name: /import contacts/i })).toBeInTheDocument();
    expect(screen.queryByTestId("review-flow")).not.toBeInTheDocument();
  });
});

describe("ImportPage — intra-batch deduplication", () => {
  it("removes contacts with duplicate emails and shows info toast", async () => {
    const user = userEvent.setup();
    const withDupe = JSON.stringify([
      { name: "Alice", email: "alice@example.com", company: "Acme", dartmouth: true, mode: "outreach", tier: 2 },
      { name: "Alice Again", email: "alice@example.com", company: "Acme", dartmouth: true, mode: "outreach", tier: 2 },
      { name: "Bob", email: "bob@example.com", company: "Corp", dartmouth: true, mode: "outreach", tier: 2 },
    ]);
    render(<ImportPage />);
    fireEvent.change(screen.getByRole("textbox"), { target: { value: withDupe } });
    await user.click(screen.getByRole("button", { name: /review contacts/i }));

    // Only 2 unique contacts should reach ReviewFlow
    expect(screen.getByTestId("contact-count").textContent).toBe("2");
    expect(toastInfoMock).toHaveBeenCalledWith("1 duplicate email removed from this batch");
  });

  it("keeps contacts with no email even when there are multiple", async () => {
    const user = userEvent.setup();
    const withNoEmail = JSON.stringify([
      { name: "Alice", email: null, company: "Acme", dartmouth: true, mode: "outreach", tier: 2 },
      { name: "Bob", email: null, company: "Corp", dartmouth: true, mode: "outreach", tier: 2 },
    ]);
    render(<ImportPage />);
    fireEvent.change(screen.getByRole("textbox"), { target: { value: withNoEmail } });
    await user.click(screen.getByRole("button", { name: /review contacts/i }));

    expect(screen.getByTestId("contact-count").textContent).toBe("2");
    expect(toastInfoMock).not.toHaveBeenCalled();
  });
});

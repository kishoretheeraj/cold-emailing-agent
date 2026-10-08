import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { PeoplePanel } from "./PeoplePanel";

vi.mock("sonner", () => {
  const toast = Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() });
  return { toast };
});
import { toast } from "sonner";

type Call = [string, RequestInit | undefined];
let calls: Call[];
let people: Record<string, unknown>;
let addResponse: { status: number; body: unknown };

function data(over: Record<string, unknown> = {}) {
  return {
    application: { company: "Acme", role: "Associate Product Manager" },
    linked: [{ id: 5, name: "Lin Ked", mode: "networking", stage: "new", reply_status: "no_reply", relationship: "alum" }],
    known: [
      { id: 6, name: "Ann Lee", role: "PM", mode: "networking", stage: "new", reply_status: "no_reply", blocker: null },
      { id: 7, name: "Bo Chan", mode: "outreach", stage: "first_touch_sent", reply_status: "no_reply", blocker: "Already in touch" },
    ],
    known_emails: [{ name: "Ann Lee", email: "ann.lee@acme.com" }, { name: "Bo Chan", email: "bo.chan@acme.com" }],
    search_links: [{ label: "LinkedIn: Dartmouth alumni", url: "https://www.linkedin.com/search/results/people/?keywords=Acme%20Dartmouth" }],
    posting_emails: [{ email: "jane@acme.com", kind: "person" }, { email: "careers@acme.com", kind: "inbox" }],
    remaining: 2, company_recent: 1, company_cap: 5, closed: false,
    ...over,
  };
}

beforeEach(() => {
  calls = [];
  people = data();
  addResponse = { status: 201, body: { contact: { id: 9 } } };
  vi.mocked(toast.success).mockReset();
  vi.mocked(toast.error).mockReset();
  vi.stubGlobal("fetch", vi.fn((url: string, init?: RequestInit) => {
    calls.push([url, init]);
    if (url === "/api/applications/3/people" && !init?.method) {
      return Promise.resolve(new Response(JSON.stringify(people), { status: 200 }));
    }
    if (url === "/api/applications/3/people") {
      return Promise.resolve(new Response(JSON.stringify(addResponse.body), { status: addResponse.status }));
    }
    return Promise.resolve(new Response(JSON.stringify({ ok: true }), { status: 200 }));
  }));
});

async function renderPanel(onChange = vi.fn()) {
  render(<PeoplePanel applicationId="3" onChange={onChange} />);
  await act(async () => {});
  return onChange;
}

const body = (i: number) => JSON.parse(String(calls[i][1]?.body));
const posts = () => calls.filter(([, init]) => init?.method === "POST");

function fill(label: string, value: string) {
  fireEvent.change(screen.getByLabelText(label), { target: { value } });
}

describe("PeoplePanel", () => {
  it("lists linked people, known contacts with their blockers, search links and posting emails", async () => {
    await renderPanel();
    expect(screen.getByText(/Linked 1 of 3/)).toBeInTheDocument();
    expect(screen.getByText("Lin Ked")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Link" })).toBeInTheDocument();
    expect(screen.getByText("Already in touch")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "LinkedIn: Dartmouth alumni" })).toHaveAttribute("target", "_blank");
    expect(screen.getByText(/careers@acme.com/)).toHaveTextContent("(inbox)");
  });

  it("links a known contact and unlinks a linked one", async () => {
    const onChange = await renderPanel();
    await act(async () => { screen.getByRole("button", { name: "Link" }).click(); });
    await act(async () => { screen.getByRole("button", { name: "Unlink" }).click(); });
    expect(posts().map(([url, init]) => [url, JSON.parse(String(init?.body)).contact_id])).toEqual([
      ["/api/applications/3/people/link", 6], ["/api/applications/3/people/unlink", 5],
    ]);
    expect(onChange).toHaveBeenCalledTimes(2);
  });

  it("adds a hiring manager with an applied-mode payload", async () => {
    await renderPanel();
    fill("Name", "Jane Doe");
    fill("Email", "jane@acme.com");
    fill("Their title", "Director of Product");
    await act(async () => { fireEvent.submit(screen.getByRole("form", { name: "Add person" })); });
    const sent = body(calls.findIndex(([url, init]) => url === "/api/applications/3/people" && init?.method === "POST"));
    expect(sent).toMatchObject({ name: "Jane Doe", email: "jane@acme.com", relationship: "hiring_manager",
      title: "Director of Product", tier: 2, school: null, connection_context: "" });
    expect(toast.success).toHaveBeenCalled();
  });

  it("an alum gets a school and a role-free hook only when the user taps the suggestion", async () => {
    await renderPanel();
    fill("Relationship", "alum");
    fill("School", "tuck");
    const connection = screen.getByLabelText("Connection") as HTMLTextAreaElement;
    expect(connection.value).toBe("");
    expect(connection.placeholder).toBe("Fellow Dartmouth Tuck alum");
    await act(async () => { screen.getByRole("button", { name: "Use suggestion" }).click(); });
    expect(connection.value).toBe("Fellow Dartmouth Tuck alum");
    fill("Name", "Al Um");
    fill("Email", "al@acme.com");
    await act(async () => { fireEvent.submit(screen.getByRole("form", { name: "Add person" })); });
    const sent = body(calls.findIndex(([, init]) => init?.method === "POST"));
    expect(sent).toMatchObject({ relationship: "alum", school: "tuck", connection_context: "Fellow Dartmouth Tuck alum" });
  });

  it("offers an email guessed from two agreeing known addresses, never filling it by itself", async () => {
    await renderPanel();
    fill("Name", "Jane Doe");
    expect((screen.getByLabelText("Email") as HTMLInputElement).value).toBe("");
    await act(async () => {
      screen.getByRole("button", { name: /Use jane.doe@acme.com \(guessed from 2 known addresses; unverified\)/ }).click();
    });
    expect((screen.getByLabelText("Email") as HTMLInputElement).value).toBe("jane.doe@acme.com");
  });

  it("a posting email fills the field when tapped", async () => {
    await renderPanel();
    await act(async () => { screen.getByRole("button", { name: "jane@acme.com" }).click(); });
    expect((screen.getByLabelText("Email") as HTMLInputElement).value).toBe("jane@acme.com");
  });

  it("offers to link an existing contact after a duplicate", async () => {
    addResponse = { status: 409, body: { error: "Ann Lee is already in your contacts", existing: { id: 6, name: "Ann Lee", blocker: null } } };
    await renderPanel();
    fill("Name", "Ann Lee");
    fill("Email", "ann.lee@acme.com");
    await act(async () => { fireEvent.submit(screen.getByRole("form", { name: "Add person" })); });
    expect(toast.error).toHaveBeenCalledWith("Ann Lee is already in your contacts");
    await act(async () => { screen.getByRole("button", { name: "Link Ann Lee instead" }).click(); });
    expect(calls.some(([url]) => url === "/api/applications/3/people/link")).toBe(true);
  });

  it("disables adding at the per-application and per-company caps", async () => {
    people = data({ remaining: 0 });
    await renderPanel();
    expect(screen.getByRole("button", { name: "Add person" })).toBeDisabled();
    expect(screen.getByText("This application has 3 people.")).toBeInTheDocument();
  });

  it("says the company cap is reached", async () => {
    people = data({ company_recent: 5 });
    await renderPanel();
    expect(screen.getByRole("button", { name: "Add person" })).toBeDisabled();
    expect(screen.getByText(/added 5 people at Acme in the last 30 days/)).toBeInTheDocument();
  });

  it("a closed application has no form", async () => {
    people = data({ closed: true });
    await renderPanel();
    expect(screen.queryByRole("form", { name: "Add person" })).toBeNull();
    expect(screen.getByText(/closed/)).toBeInTheDocument();
  });

  it("says when it could not load", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(new Response("{}", { status: 500 }))));
    await renderPanel();
    expect(screen.getByText("Could not load the people for this application.")).toBeInTheDocument();
  });
});

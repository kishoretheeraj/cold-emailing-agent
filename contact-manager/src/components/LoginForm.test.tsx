import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { LoginForm } from "./LoginForm";

const assign = vi.fn();

beforeEach(() => {
  assign.mockReset();
  Object.defineProperty(window, "location", { value: { ...window.location, assign }, writable: true });
});

function respond(status: number, body: unknown = {}) {
  vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(new Response(JSON.stringify(body), { status }))));
}

describe("LoginForm", () => {
  it("disables Sign in until a password is typed", async () => {
    render(<LoginForm />);
    expect(screen.getByRole("button", { name: "Sign in" })).toBeDisabled();
    await userEvent.type(screen.getByLabelText("Password"), "x");
    expect(screen.getByRole("button", { name: "Sign in" })).toBeEnabled();
  });

  it("posts the password and goes to the requested page", async () => {
    respond(200, { ok: true });
    render(<LoginForm next="/applications" />);
    await userEvent.type(screen.getByLabelText("Password"), "correct horse battery");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    const [url, init] = vi.mocked(fetch).mock.calls[0];
    expect(url).toBe("/api/login");
    expect(JSON.parse((init as RequestInit).body as string)).toEqual({ password: "correct horse battery" });
    expect(assign).toHaveBeenCalledWith("/applications");
  });

  it("never redirects off-site", async () => {
    respond(200, { ok: true });
    render(<LoginForm next="https://evil.example" />);
    await userEvent.type(screen.getByLabelText("Password"), "pw");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(assign).toHaveBeenCalledWith("/");
  });

  it("shows Wrong password on a 401 and stays on the page", async () => {
    respond(401, { error: "Wrong password" });
    render(<LoginForm />);
    await userEvent.type(screen.getByLabelText("Password"), "nope");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Wrong password.");
    expect(assign).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Sign in" })).toBeEnabled();
  });

  it("shows the server's reason when login is not configured", async () => {
    respond(503, { error: "Login is not configured (OPERATOR_PASSWORD, SESSION_SECRET)" });
    render(<LoginForm />);
    await userEvent.type(screen.getByLabelText("Password"), "pw");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Login is not configured");
  });
});

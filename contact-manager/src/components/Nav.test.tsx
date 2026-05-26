import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
  Toaster: () => null,
}));

vi.mock("next/navigation", () => ({
  usePathname: vi.fn(() => "/"),
}));

import { usePathname } from "next/navigation";
import { Nav } from "./Nav";

beforeEach(() => {
  vi.mocked(usePathname).mockReturnValue("/");
});

describe("Nav", () => {
  it("renders the app title linking to home", () => {
    render(<Nav />);
    const homeLink = screen.getByRole("link", { name: /cold email ops/i });
    expect(homeLink).toBeInTheDocument();
    expect(homeLink).toHaveAttribute("href", "/");
  });

  it("renders Overview nav link pointing to /overview", () => {
    render(<Nav />);
    const link = screen.getByRole("link", { name: /^overview$/i });
    expect(link).toBeInTheDocument();
    expect(link).toHaveAttribute("href", "/overview");
  });

  it("renders Queue nav link pointing to /queue", () => {
    render(<Nav />);
    const link = screen.getByRole("link", { name: /^queue$/i });
    expect(link).toBeInTheDocument();
    expect(link).toHaveAttribute("href", "/queue");
  });

  it("renders Replies nav link pointing to /replies", () => {
    render(<Nav />);
    const link = screen.getByRole("link", { name: /^replies$/i });
    expect(link).toBeInTheDocument();
    expect(link).toHaveAttribute("href", "/replies");
  });

  it("renders Prompts nav link pointing to /prompts", () => {
    render(<Nav />);
    const link = screen.getByRole("link", { name: /^prompts$/i });
    expect(link).toBeInTheDocument();
    expect(link).toHaveAttribute("href", "/prompts");
  });

  it("renders Activity nav link pointing to /runs", () => {
    render(<Nav />);
    const link = screen.getByRole("link", { name: /activity/i });
    expect(link).toBeInTheDocument();
    expect(link).toHaveAttribute("href", "/runs");
  });

  it("renders Run Agent button", () => {
    render(<Nav />);
    expect(screen.getByRole("button", { name: /run agent/i })).toBeInTheDocument();
  });

  it("highlights the active route link", () => {
    vi.mocked(usePathname).mockReturnValue("/queue");
    render(<Nav />);
    const queueLink = screen.getByRole("link", { name: /^queue$/i });
    expect(queueLink.className).toMatch(/indigo/);
    const overviewLink = screen.getByRole("link", { name: /^overview$/i });
    expect(overviewLink.className).not.toMatch(/indigo/);
  });

  it("calls trigger-agent on Run Agent click", async () => {
    const user = userEvent.setup();
    global.fetch = vi.fn().mockResolvedValue({ ok: true });
    render(<Nav />);
    await user.click(screen.getByRole("button", { name: /run agent/i }));
    expect(global.fetch).toHaveBeenCalledWith("/api/trigger-agent", { method: "POST" });
  });
});

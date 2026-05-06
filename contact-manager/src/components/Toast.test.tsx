import { describe, it, expect, vi } from "vitest";
import { render, screen, act } from "@testing-library/react";

import { Toast } from "./Toast";

describe("Toast", () => {
  it("shows the message", () => {
    render(<Toast message="hello world" onClose={() => {}} />);
    expect(screen.getByText("hello world")).toBeInTheDocument();
  });

  it("auto-dismisses after 4 seconds", () => {
    vi.useFakeTimers();
    const onClose = vi.fn();
    render(<Toast message="bye" onClose={onClose} />);

    expect(onClose).not.toHaveBeenCalled();
    act(() => {
      vi.advanceTimersByTime(4000);
    });
    expect(onClose).toHaveBeenCalledOnce();
    vi.useRealTimers();
  });

  it("applies error styling for tone=error", () => {
    render(<Toast message="bad" tone="error" onClose={() => {}} />);
    const toast = screen.getByRole("status");
    expect(toast.className).toMatch(/red/);
  });

  it("applies success styling by default", () => {
    render(<Toast message="ok" onClose={() => {}} />);
    const toast = screen.getByRole("status");
    expect(toast.className).toMatch(/emerald/);
  });
});

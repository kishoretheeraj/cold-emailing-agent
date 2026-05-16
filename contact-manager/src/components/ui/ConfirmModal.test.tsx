import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ConfirmModal } from "./ConfirmModal";

const DEFAULT_PROPS = {
  open: true,
  title: "Are you sure?",
  body: <p>This action cannot be undone.</p>,
  confirmLabel: "Confirm",
  onConfirm: vi.fn(),
  onCancel: vi.fn(),
};

describe("ConfirmModal", () => {
  it("renders title and body when open", () => {
    render(<ConfirmModal {...DEFAULT_PROPS} />);
    expect(screen.getByText("Are you sure?")).toBeInTheDocument();
    expect(screen.getByText("This action cannot be undone.")).toBeInTheDocument();
  });

  it("renders nothing when open=false", () => {
    render(<ConfirmModal {...DEFAULT_PROPS} open={false} />);
    expect(screen.queryByText("Are you sure?")).toBeNull();
  });

  it("calls onCancel when Cancel button is clicked", async () => {
    const onCancel = vi.fn();
    const user = userEvent.setup();
    render(<ConfirmModal {...DEFAULT_PROPS} onCancel={onCancel} />);
    await user.click(screen.getByRole("button", { name: /cancel/i }));
    expect(onCancel).toHaveBeenCalledOnce();
  });

  it("calls onConfirm when confirm button is clicked", async () => {
    const onConfirm = vi.fn();
    const user = userEvent.setup();
    render(<ConfirmModal {...DEFAULT_PROPS} onConfirm={onConfirm} />);
    await user.click(screen.getByRole("button", { name: /confirm/i }));
    expect(onConfirm).toHaveBeenCalledOnce();
  });

  it("disables both buttons when loading=true", () => {
    render(<ConfirmModal {...DEFAULT_PROPS} loading={true} />);
    const buttons = screen.getAllByRole("button");
    buttons.forEach((btn) => {
      expect(btn).toBeDisabled();
    });
  });

  it("shows spinner icon when loading=true", () => {
    render(<ConfirmModal {...DEFAULT_PROPS} loading={true} confirmLabel="Delete" />);
    // The Loader2 icon renders as an SVG inside the confirm button
    const confirmBtn = screen.getByRole("button", { name: /delete/i });
    expect(confirmBtn.querySelector("svg")).toBeTruthy();
  });

  it("uses danger styling when confirmVariant=danger", () => {
    render(
      <ConfirmModal {...DEFAULT_PROPS} confirmVariant="danger" confirmLabel="Delete" />
    );
    const confirmBtn = screen.getByRole("button", { name: /delete/i });
    expect(confirmBtn.className).toMatch(/red/);
  });

  it("triggers onCancel when Escape key is pressed", async () => {
    const onCancel = vi.fn();
    const user = userEvent.setup();
    render(<ConfirmModal {...DEFAULT_PROPS} onCancel={onCancel} />);
    await user.keyboard("{Escape}");
    expect(onCancel).toHaveBeenCalled();
  });
});

import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { Label, TextInput, TextArea, ToggleSwitch, TierSelector } from "./Field";

describe("Label", () => {
  it("renders an asterisk when required", () => {
    render(<Label required>Name</Label>);
    expect(screen.getByText("Name")).toBeInTheDocument();
    expect(screen.getByText("*")).toBeInTheDocument();
  });

  it("omits the asterisk by default", () => {
    render(<Label>Name</Label>);
    expect(screen.queryByText("*")).toBeNull();
  });
});

describe("TextInput", () => {
  it("forwards value and change events", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<TextInput value="" onChange={onChange} aria-label="email" />);

    const input = screen.getByLabelText("email");
    await user.type(input, "x");
    expect(onChange).toHaveBeenCalled();
  });
});

describe("TextArea", () => {
  it("renders content", () => {
    render(<TextArea value="hello" onChange={() => {}} aria-label="bio" />);
    expect(screen.getByLabelText("bio")).toHaveValue("hello");
  });
});

describe("ToggleSwitch", () => {
  it("calls onChange with the inverted value when clicked", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<ToggleSwitch on={false} onChange={onChange} label="Dartmouth" />);

    await user.click(screen.getByText("Dartmouth"));
    expect(onChange).toHaveBeenCalledWith(true);
  });

  it("flips back when on=true", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<ToggleSwitch on={true} onChange={onChange} label="Dartmouth" />);

    await user.click(screen.getByText("Dartmouth"));
    expect(onChange).toHaveBeenCalledWith(false);
  });
});

describe("TierSelector", () => {
  it("renders three tiers", () => {
    render(<TierSelector value={2} onChange={() => {}} />);
    expect(screen.getByText(/Tier 1/)).toBeInTheDocument();
    expect(screen.getByText(/Tier 2/)).toBeInTheDocument();
    expect(screen.getByText(/Tier 3/)).toBeInTheDocument();
  });

  it("emits the chosen tier value", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<TierSelector value={2} onChange={onChange} />);

    await user.click(screen.getByText(/Tier 1/));
    expect(onChange).toHaveBeenCalledWith(1);

    await user.click(screen.getByText(/Tier 3/));
    expect(onChange).toHaveBeenCalledWith(3);
  });
});

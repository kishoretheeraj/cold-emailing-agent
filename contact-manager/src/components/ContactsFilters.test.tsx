import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ContactsFilters } from "./ContactsFilters";
import {
  type ContactsQueryFilters,
  EMPTY_FILTERS,
  filtersEqual,
} from "@/lib/types";

vi.mock("@radix-ui/react-tooltip", () => ({
  Provider: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  Root: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  Trigger: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  Portal: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  Content: () => null,
  Arrow: () => null,
  TooltipProvider: ({ children }: { children: React.ReactNode }) => (
    <>{children}</>
  ),
}));

// Minimal Select mock that renders options and fires onValueChange on click
vi.mock("@radix-ui/react-select", () => {
  let _onValueChange: ((v: string) => void) | undefined;

  return {
    Root: ({
      children,
      value,
      onValueChange,
    }: {
      children: React.ReactNode;
      value?: string;
      onValueChange?: (v: string) => void;
    }) => {
      _onValueChange = onValueChange;
      return <div data-select-value={value}>{children}</div>;
    },
    Trigger: ({ children }: { children: React.ReactNode }) => (
      <button type="button">{children}</button>
    ),
    Value: ({ placeholder }: { placeholder?: string }) => (
      <span>{placeholder}</span>
    ),
    Icon: ({ children }: { children: React.ReactNode }) => <>{children}</>,
    Portal: ({ children }: { children: React.ReactNode }) => <>{children}</>,
    Content: ({ children }: { children: React.ReactNode }) => (
      <div>{children}</div>
    ),
    Viewport: ({ children }: { children: React.ReactNode }) => <>{children}</>,
    Group: ({ children }: { children: React.ReactNode }) => <>{children}</>,
    Label: ({ children }: { children: React.ReactNode }) => (
      <div>{children}</div>
    ),
    Item: ({
      children,
      value,
    }: {
      children: React.ReactNode;
      value: string;
    }) => (
      <div
        role="option"
        data-value={value}
        onClick={() => _onValueChange?.(value)}
      >
        {children}
      </div>
    ),
    ItemText: ({ children }: { children: React.ReactNode }) => <>{children}</>,
    ItemIndicator: () => null,
    Separator: () => <hr />,
  };
});

import { useState } from "react";

function renderFilters(
  initial: ContactsQueryFilters = EMPTY_FILTERS,
  onChange = vi.fn()
) {
  // Stateful wrapper so the controlled input updates correctly between keypresses
  function Wrapper() {
    const [filters, setFilters] = useState(initial);
    return (
      <ContactsFilters
        filters={filters}
        onChange={(next) => {
          setFilters(next);
          onChange(next);
        }}
      />
    );
  }
  const result = render(<Wrapper />);
  return { ...result, onChange };
}

describe("ContactsFilters", () => {
  it("renders with empty/default state", () => {
    renderFilters();
    expect(
      screen.getByPlaceholderText("Search by name or company")
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "1" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "2" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "3" })).toBeInTheDocument();
  });

  it("search input change calls onChange with updated nameOrCompany", async () => {
    const user = userEvent.setup();
    const { onChange } = renderFilters();
    const input = screen.getByPlaceholderText("Search by name or company");
    await user.type(input, "Kishore");
    const lastCall = onChange.mock.calls[onChange.mock.calls.length - 1][0];
    expect(lastCall.nameOrCompany).toBe("Kishore");
  });

  it("tier pill click toggles membership in filters.tiers", async () => {
    const user = userEvent.setup();
    const { onChange } = renderFilters();
    await user.click(screen.getByRole("button", { name: "1" }));
    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({ tiers: [1] })
    );
  });

  it("clicking active tier pill removes it from filters.tiers", async () => {
    const user = userEvent.setup();
    const { onChange } = renderFilters({
      ...EMPTY_FILTERS,
      tiers: [1],
    });
    await user.click(screen.getByRole("button", { name: "1" }));
    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({ tiers: [] })
    );
  });

  it("mode pill click toggles membership in filters.modes", async () => {
    const user = userEvent.setup();
    const { onChange } = renderFilters();
    await user.click(screen.getByRole("button", { name: /outreach/i }));
    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({ modes: ["outreach"] })
    );
  });

  it("stage Select option click updates filters.stages", async () => {
    const user = userEvent.setup();
    const { onChange } = renderFilters();
    const option = screen.getByRole("option", { name: /first touch drafted/i });
    await user.click(option);
    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({ stages: ["first_touch_drafted"] })
    );
  });

  it("selecting 'All stages' sets filters.stages to []", async () => {
    const user = userEvent.setup();
    const { onChange } = renderFilters({
      ...EMPTY_FILTERS,
      stages: ["first_touch_drafted"],
    });
    const allOption = screen.getByRole("option", { name: "All stages" });
    await user.click(allOption);
    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({ stages: [] })
    );
  });

  it("dartmouth toggle updates filters.dartmouthOnly", async () => {
    const user = userEvent.setup();
    const { onChange } = renderFilters();
    const toggle = screen.getByRole("button", { name: /dartmouth/i });
    await user.click(toggle);
    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({ dartmouthOnly: true })
    );
  });

  it("clear button is hidden when filters equal EMPTY_FILTERS", () => {
    renderFilters(EMPTY_FILTERS);
    expect(screen.queryByRole("button", { name: /clear filters/i })).toBeNull();
  });

  it("clear button appears when a filter is active", () => {
    renderFilters({ ...EMPTY_FILTERS, tiers: [1] });
    expect(
      screen.getByRole("button", { name: /clear filters/i })
    ).toBeInTheDocument();
  });

  it("clear button click calls onChange with EMPTY_FILTERS", async () => {
    const user = userEvent.setup();
    const { onChange } = renderFilters({ ...EMPTY_FILTERS, tiers: [2] });
    await user.click(screen.getByRole("button", { name: /clear filters/i }));
    const called = onChange.mock.calls[0][0] as ContactsQueryFilters;
    expect(filtersEqual(called, EMPTY_FILTERS)).toBe(true);
  });
});

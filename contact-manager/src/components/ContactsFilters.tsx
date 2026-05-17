"use client";

import { Search } from "lucide-react";
import { TextInput, ToggleSwitch } from "@/components/Field";
import { Tooltip } from "@/components/ui/Tooltip";
import {
  Select,
  SelectTrigger,
  SelectValue,
  SelectContent,
  SelectItem,
  SelectGroup,
  SelectLabel,
  SelectSeparator,
} from "@/components/ui/Select";
import {
  type ContactsQueryFilters,
  EMPTY_FILTERS,
  OUTREACH_STAGES,
  APPLIED_STAGES,
  filtersEqual,
} from "@/lib/types";

const TIER_TOOLTIPS: Record<number, string> = {
  1: "Top priority — dream targets",
  2: "Strong targets",
  3: "Backup / low effort",
};

// Outreach stages without "new" and "closed" (shown separately)
const OUTREACH_FILTER_STAGES = OUTREACH_STAGES.filter(
  (s) => s !== "new" && s !== "closed"
);
const APPLIED_FILTER_STAGES = APPLIED_STAGES.filter(
  (s) => s !== "new" && s !== "closed"
);

function formatStageLabel(s: string): string {
  return s.replace(/_/g, " ");
}

type Props = {
  filters: ContactsQueryFilters;
  onChange: (next: ContactsQueryFilters) => void;
};

export function ContactsFilters({ filters, onChange }: Props) {
  const stageValue =
    filters.stages.length === 0 ? "__all__" : filters.stages[0];

  function toggleTier(tier: number) {
    const next = filters.tiers.includes(tier)
      ? filters.tiers.filter((t) => t !== tier)
      : [...filters.tiers, tier];
    onChange({ ...filters, tiers: next });
  }

  function toggleMode(mode: "outreach" | "applied") {
    const next = filters.modes.includes(mode)
      ? filters.modes.filter((m) => m !== mode)
      : [...filters.modes, mode];
    onChange({ ...filters, modes: next });
  }

  function handleStageChange(v: string) {
    onChange({ ...filters, stages: v === "__all__" ? [] : [v] });
  }

  const isEmpty = filtersEqual(filters, EMPTY_FILTERS);

  return (
    <div className="bg-surface border border-border rounded-lg px-4 py-3 mb-3">
      {/* Row 1: search */}
      <div className="relative">
        <Search className="absolute left-3 top-1/2 -translate-y-1/2 size-4 text-fg-dim pointer-events-none" />
        <TextInput
          placeholder="Search by name or company"
          value={filters.nameOrCompany}
          onChange={(e) =>
            onChange({ ...filters, nameOrCompany: e.target.value })
          }
          className="pl-9"
        />
      </div>

      {/* Row 2: pill filters */}
      <div className="flex flex-wrap gap-3 items-center mt-3">
        {/* Tier */}
        <div className="flex items-center gap-1.5">
          <span className="text-fg-dim text-xs uppercase tracking-wider">
            Tier
          </span>
          {[1, 2, 3].map((tier) => {
            const active = filters.tiers.includes(tier);
            return (
              <Tooltip key={tier} content={TIER_TOOLTIPS[tier]}>
                <button
                  type="button"
                  onClick={() => toggleTier(tier)}
                  className={`rounded-full px-3 py-1 text-sm border transition-colors focus:outline-none focus:ring-2 focus:ring-indigo-500/40 ${
                    active
                      ? "bg-indigo-500/20 text-indigo-300 border-indigo-500/40"
                      : "bg-surface-2 text-fg-muted border-border hover:border-border-strong"
                  }`}
                >
                  {tier}
                </button>
              </Tooltip>
            );
          })}
        </div>

        {/* Mode */}
        <div className="flex items-center gap-1.5">
          <span className="text-fg-dim text-xs uppercase tracking-wider">
            Mode
          </span>
          {(["outreach", "applied"] as const).map((mode) => {
            const active = filters.modes.includes(mode);
            return (
              <button
                key={mode}
                type="button"
                onClick={() => toggleMode(mode)}
                className={`rounded-full px-3 py-1 text-sm border transition-colors capitalize focus:outline-none focus:ring-2 focus:ring-indigo-500/40 ${
                  active
                    ? "bg-indigo-500/20 text-indigo-300 border-indigo-500/40"
                    : "bg-surface-2 text-fg-muted border-border hover:border-border-strong"
                }`}
              >
                {mode}
              </button>
            );
          })}
        </div>

        {/* Stage */}
        <div className="flex items-center gap-1.5">
          <span className="text-fg-dim text-xs uppercase tracking-wider">
            Stage
          </span>
          <Select value={stageValue} onValueChange={handleStageChange}>
            <SelectTrigger className="h-8 text-xs py-1">
              <SelectValue placeholder="All stages" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="__all__">All stages</SelectItem>
              <SelectSeparator />
              <SelectGroup>
                <SelectLabel>Outreach</SelectLabel>
                {OUTREACH_FILTER_STAGES.map((s) => (
                  <SelectItem key={s} value={s}>
                    {formatStageLabel(s)}
                  </SelectItem>
                ))}
              </SelectGroup>
              <SelectGroup>
                <SelectLabel>Applied</SelectLabel>
                {APPLIED_FILTER_STAGES.map((s) => (
                  <SelectItem key={s} value={s}>
                    {formatStageLabel(s)}
                  </SelectItem>
                ))}
              </SelectGroup>
              <SelectSeparator />
              <SelectItem value="new">New</SelectItem>
              <SelectItem value="closed">Closed</SelectItem>
            </SelectContent>
          </Select>
        </div>

        {/* Dartmouth toggle */}
        <ToggleSwitch
          on={filters.dartmouthOnly}
          onChange={(v) => onChange({ ...filters, dartmouthOnly: v })}
          label="Dartmouth alumni only"
        />

        {/* Needs response toggle */}
        <ToggleSwitch
          on={filters.needsResponseOnly}
          onChange={(v) => onChange({ ...filters, needsResponseOnly: v })}
          label="Needs response"
        />
      </div>

      {/* Row 3: clear button */}
      {!isEmpty && (
        <div className="flex justify-end mt-2">
          <button
            type="button"
            onClick={() => onChange(EMPTY_FILTERS)}
            className="text-fg-muted text-sm hover:text-fg transition-colors border border-transparent hover:border-border rounded-md px-2 py-1"
          >
            Clear filters
          </button>
        </div>
      )}
    </div>
  );
}

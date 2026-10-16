"use client";

import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/Select";
import {
  JOB_APPLICATION_STAGES,
  JOB_APPLICATION_STAGE_LABELS,
} from "@/lib/types";

const SOURCE_OPTIONS = ["linkedin", "ats_scan", "jobright", "manual"] as const;

interface ApplicationFiltersProps {
  stageFilter: string;
  sourceFilter: string;
  onStageChange: (v: string) => void;
  onSourceChange: (v: string) => void;
}

export function ApplicationFilters({
  stageFilter,
  sourceFilter,
  onStageChange,
  onSourceChange,
}: ApplicationFiltersProps) {
  return (
    <div className="flex gap-3">
      <label
        data-testid="stage-filter"
        className="flex flex-col gap-1 text-sm text-fg-muted"
      >
        Stage
        <Select value={stageFilter} onValueChange={onStageChange}>
          <SelectTrigger>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="__all__">All stages</SelectItem>
            {JOB_APPLICATION_STAGES.map((s) => (
              <SelectItem key={s} value={s}>
                {JOB_APPLICATION_STAGE_LABELS[s]}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </label>
      <label
        data-testid="source-filter"
        className="flex flex-col gap-1 text-sm text-fg-muted"
      >
        Source
        <Select value={sourceFilter} onValueChange={onSourceChange}>
          <SelectTrigger>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="__all__">All sources</SelectItem>
            {SOURCE_OPTIONS.map((s) => (
              <SelectItem key={s} value={s}>
                {s}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </label>
    </div>
  );
}

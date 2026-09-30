import type { JobApplicationPickVerdict } from "@/lib/types";
import type { BadgeVariant } from "@/components/ui/Badge";

export function pickVerdictVariant(verdict: JobApplicationPickVerdict | null): BadgeVariant {
  if (verdict === "strong") return "emerald";
  if (verdict === "maybe") return "amber";
  if (verdict === "no") return "red";
  return "default";
}

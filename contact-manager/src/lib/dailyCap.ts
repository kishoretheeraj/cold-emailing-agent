export const DEFAULT_DAILY_CAP = 50;

// The same cap the Beelink's submit worker enforces (job_search_preferences.daily_submit_cap;
// 0 means no cap). A missing or malformed row falls back to the default, as job_filters.py does.
export function dailyCap(raw: unknown): number {
  try {
    const parsed: unknown = typeof raw === "string" ? JSON.parse(raw) : null;
    if (parsed && typeof parsed === "object") {
      const cap = (parsed as Record<string, unknown>).daily_submit_cap;
      if (typeof cap === "number" && Number.isInteger(cap) && cap >= 0) return cap;
    }
  } catch {
    // fall through to the default
  }
  return DEFAULT_DAILY_CAP;
}

// The submit worker counts its daily cap in America/New_York days (apply_worker._daily_room); the
// queue's "today" meter must start the day at the same instant.
export function startOfNewYorkDay(now: Date = new Date()): Date {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: "America/New_York", year: "numeric", month: "2-digit", day: "2-digit",
    timeZoneName: "shortOffset",
  }).formatToParts(now);
  const get = (type: string) => parts.find((p) => p.type === type)?.value ?? "";
  const match = /GMT([+-])(\d{1,2})(?::(\d{2}))?/.exec(get("timeZoneName"));
  const offsetMinutes = match ? (match[1] === "-" ? -1 : 1) * (Number(match[2]) * 60 + Number(match[3] ?? 0)) : 0;
  const midnightUtc = Date.UTC(Number(get("year")), Number(get("month")) - 1, Number(get("day")));
  return new Date(midnightUtc - offsetMinutes * 60_000);
}

// Today's calendar date in New York as YYYY-MM-DD (a DATE column value).
export function newYorkDate(now: Date = new Date()): string {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: "America/New_York", year: "numeric", month: "2-digit", day: "2-digit",
  }).format(now);
}

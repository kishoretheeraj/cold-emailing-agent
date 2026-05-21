import type { Contact } from "@/lib/types";

export type Segment = { text: string; highlighted: boolean };

type Range = { start: number; end: number };

function escapeRegex(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function collectRanges(body: string, contact: Contact): Range[] {
  const ranges: Range[] = [];
  const bodyLower = body.toLowerCase();

  function addWordMatch(term: string) {
    if (!term) return;
    const escaped = escapeRegex(term);
    const re = new RegExp(`\\b${escaped}\\b`, "gi");
    for (const m of body.matchAll(re)) {
      ranges.push({ start: m.index!, end: m.index! + m[0].length });
    }
  }

  function addSubstringMatch(term: string) {
    if (!term) return;
    const termLower = term.toLowerCase();
    let idx = bodyLower.indexOf(termLower);
    while (idx !== -1) {
      ranges.push({ start: idx, end: idx + term.length });
      idx = bodyLower.indexOf(termLower, idx + 1);
    }
  }

  // First name (first token of contact.name)
  const firstName = contact.name?.split(/\s+/)[0];
  if (firstName) addWordMatch(firstName);

  // Company
  if (contact.company) addWordMatch(contact.company);

  // Detail (first 40 chars substring)
  const detailSnippet = contact.detail?.slice(0, 40);
  if (detailSnippet) addSubstringMatch(detailSnippet);

  // T'YY alumni pattern
  for (const m of body.matchAll(/\bT'\d{2}\b/g)) {
    ranges.push({ start: m.index!, end: m.index! + m[0].length });
  }

  // Named keywords
  for (const kw of ["Dartmouth", "Tuck", "Thayer"]) {
    addWordMatch(kw);
  }

  return ranges;
}

function mergeRanges(ranges: Range[]): Range[] {
  if (ranges.length === 0) return [];
  const sorted = [...ranges].sort((a, b) => a.start - b.start || b.end - a.end);
  const merged: Range[] = [sorted[0]];
  for (let i = 1; i < sorted.length; i++) {
    const last = merged[merged.length - 1];
    if (sorted[i].start < last.end) {
      last.end = Math.max(last.end, sorted[i].end);
    } else {
      merged.push(sorted[i]);
    }
  }
  return merged;
}

export function highlight(body: string, contact: Contact): Segment[] {
  const raw = collectRanges(body, contact);
  const merged = mergeRanges(raw);

  const segments: Segment[] = [];
  let cursor = 0;
  for (const { start, end } of merged) {
    if (cursor < start) {
      segments.push({ text: body.slice(cursor, start), highlighted: false });
    }
    segments.push({ text: body.slice(start, end), highlighted: true });
    cursor = end;
  }
  if (cursor < body.length) {
    segments.push({ text: body.slice(cursor), highlighted: false });
  }
  return segments.length > 0 ? segments : [{ text: body, highlighted: false }];
}

// Matches single-brace {identifier} only; \w+ requires word chars so
// patterns with spaces (e.g., {{multi word}}) never match.
export function extractVariables(template: string): string[] {
  const matches = template.matchAll(/\{(\w+)\}/g);
  const seen = new Set<string>();
  const out: string[] = [];
  for (const m of matches) {
    if (!seen.has(m[1])) {
      seen.add(m[1]);
      out.push(m[1]);
    }
  }
  return out;
}

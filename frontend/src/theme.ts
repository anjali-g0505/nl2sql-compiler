// Recharts takes concrete colour values, not CSS variables, so read them back from
// tokens.css at run time. That keeps tokens.css the only place a hex is written.

const cache = new Map<string, string>();

export function token(name: string): string {
  const cached = cache.get(name);
  if (cached) return cached;
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  const resolved = value.startsWith("var(")
    ? token(value.slice(4, -1).trim())  // tokens.css aliases some names to others
    : value;
  cache.set(name, resolved);
  return resolved;
}

/** Series colours in order, matching --series-1..4 and the syntax highlighting. */
export function seriesColors(): string[] {
  return ["--series-1", "--series-2", "--series-3", "--series-4"].map(token);
}

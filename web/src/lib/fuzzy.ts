// The command palette's match: each letter in order, closer together and at the start of a word scoring higher.
export function fuzzyScore(needle: string, hay: string): number {
  const n = needle.toLowerCase(), h = hay.toLowerCase();
  if (!n) return 0;
  let from = 0, score = 0, run = 0;
  for (const ch of n) {
    const found = h.indexOf(ch, from);
    if (found < 0) return -Infinity;
    run = found === from ? run + 1 : 0;
    score += 2 + run * 2 + (found === 0 || " /·-".includes(h[found - 1]!) ? 3 : 0) - (found - from);
    from = found + 1;
  }
  return score - h.length * 0.01;     // a shorter one wins a tie
}

// With no query, the entries as given, which keeps them in their groups.
export function rankByLabel<T extends { label: string }>(query: string, entries: T[]): T[] {
  if (!query) return entries;
  return entries
    .map((e) => [fuzzyScore(query, e.label), e] as const)
    .filter(([s]) => s > -Infinity)
    .sort((a, b) => b[0] - a[0])
    .map(([, e]) => e);
}

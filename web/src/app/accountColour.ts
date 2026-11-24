/** How many colours the palette holds for accounts (`--acct-1` to `--acct-6`). */
export const ACCOUNT_COLOURS = 6;

/** The colour `account` wears: the place the server keeps for it (the digest's `colours`), so removing an account moves no
 * other's and the Mac colours it the same; else its place among `accounts`, as before any was kept. */
export function accountColour(colours: Record<string, number>, accounts: string[], account: string): string {
  const place = colours[account] ?? Math.max(0, accounts.indexOf(account));
  return `var(--acct-${(place % ACCOUNT_COLOURS) + 1})`;
}

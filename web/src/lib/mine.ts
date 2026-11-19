// Which addresses are a person's own: the composer asks it of whom a message goes to, the list of whom a row is from.

// you+tag@host names the mailbox you@host does, on every server that reads a tag at all.
export function untagged(a: string): string {
  const at = a.lastIndexOf("@"), plus = a.indexOf("+");
  return plus > 0 && plus < at ? a.slice(0, plus) + a.slice(at) : a;
}

export function isMine(a: string, mine: ReadonlySet<string>): boolean {
  return mine.has(a) || mine.has(untagged(a));
}

// Your addresses as a set to ask isMine of. An account that is itself tagged has its plain mailbox along with it.
export function mineSet(addrs: string[]): Set<string> {
  return new Set(addrs.map((a) => a.toLowerCase()).flatMap((a) => [a, untagged(a)]));
}

// What the page names mail by. A message is its key, the same on every copy of it and never another message's; a copy is
// its key and the folder it is in. A row's number on the server is no name: a move, an Undo or a renumber gives the copy a
// new one, so a page that kept it could act on another message.
export interface Copy { key: number; folder: string }

export const copyOf = (x: Copy): Copy => ({ key: x.key, folder: x.folder });

// One string for a copy, for a set, a map or a React key. The key is a number, so the first colon ends it.
export const copyId = (c: Copy) => `${c.key}:${c.folder}`;

export const sameCopy = (a: Copy | null | undefined, b: Copy | null | undefined) =>
  Boolean(a && b && a.key === b.key && a.folder === b.folder);

// A copy as a request's query names it.
export const copyQuery = (c: Copy) => `key=${c.key}&folder=${encodeURIComponent(c.folder)}`;

// The first row of each copy: a server can hold two of one message in a folder (a draft saved again under its Message-ID),
// and a list shows the copy once.
export function eachCopyOnce<T extends Copy>(rows: T[]): T[] {
  const seen = new Set<string>();
  return rows.filter((r) => {
    const id = copyId(r);
    if (seen.has(id)) return false;
    seen.add(id);
    return true;
  });
}

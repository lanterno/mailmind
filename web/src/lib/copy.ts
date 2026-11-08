// What the page names mail by. A message is its key, the same on every copy of it and never another message's; a copy is
// its key, the folder it is in and its uid there. Two copies in one folder can share a key (a sender who reuses a
// Message-ID, a list's copy beside the one sent straight), so the uid says which: the server gives each copy in a folder
// its own. A row's number in the database is no name: a move, an Undo or a renumber gives the copy a new one, so a page
// that kept it could act on another message.
export interface Copy { key: number; folder: string; uid: number }

export const copyOf = (x: Copy): Copy => ({ key: x.key, folder: x.folder, uid: x.uid });

// One string for a copy, for a set, a map or a React key. The key and the uid are numbers, so the second colon ends them.
export const copyId = (c: Copy) => `${c.key}:${c.uid}:${c.folder}`;

// What `copyId` gave back as a copy.
export function copyFromId(id: string): Copy {
  const a = id.indexOf(":"), b = id.indexOf(":", a + 1);
  return { key: Number(id.slice(0, a)), uid: Number(id.slice(a + 1, b)), folder: id.slice(b + 1) };
}

export const sameCopy = (a: Copy | null | undefined, b: Copy | null | undefined) =>
  Boolean(a && b && a.key === b.key && a.folder === b.folder && a.uid === b.uid);

// A copy as a request's query names it.
export const copyQuery = (c: Copy) => `key=${c.key}&folder=${encodeURIComponent(c.folder)}&uid=${c.uid}`;

// The first row of each copy: pages of a list that moved between them can hold one twice.
export function eachCopyOnce<T extends Copy>(rows: T[]): T[] {
  const seen = new Set<string>();
  return rows.filter((r) => {
    const id = copyId(r);
    if (seen.has(id)) return false;
    seen.add(id);
    return true;
  });
}

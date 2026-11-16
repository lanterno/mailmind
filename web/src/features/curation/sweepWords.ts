// What a sweep says in plain words: what Undo brings back of one.

/** What Undo does for a sweep, when `noUndo` of what goes has no Message-ID to find it by in the Trash. */
export const undoBrings = (noUndo: number | null | undefined) => (noUndo ? "puts the rest back" : "puts it all back");

// What a sweep says in plain words: what Undo brings back of one, and why one stopped, with what to do next. The server
// names what went wrong by its class, which follows in brackets for whoever looks into it, never alone.

/** What Undo does for a sweep, when `noUndo` of what goes has no Message-ID to find it by in the Trash. */
export const undoBrings = (noUndo: number | null | undefined) => (noUndo ? "puts the rest back" : "puts it all back");

/** A sweep the server stopped, at its plan or as it moved mail, after `moved` went. `undoable` when the server kept a
 * way back though none was counted: it couldn't be asked again how much went, so some may be in the Trash already. */
export function sweepStopped(error: string, moved = 0, undoable = false): string {
  const why = moved
    ? `Couldn’t reach the mail server, so the sweep stopped after ${moved}. Undo brings ${moved === 1 ? "it" : "them"} back; sweep again for the rest.`
    : undoable
      ? "Couldn’t reach the mail server, so the sweep stopped part way and MailMind couldn’t check how much went. Undo puts back whatever did; sweep again for the rest."
      : "Couldn’t reach the mail server, so nothing was swept. Try again in a moment.";
  return `${why} (${error})`;
}

/** A sweep, or its plan, that got no answer: the server's own words when it refused, else what to do. */
export const sweepFailed = (refused: string | undefined, planning: boolean) =>
  refused || (planning ? "Couldn’t look at this Inbox. Try again in a moment." : "The sweep didn’t finish. Try again in a moment.");

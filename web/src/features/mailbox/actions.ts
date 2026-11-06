// What you can do to a message from anywhere: the list, the reader, the keyboard. Filing hides the row at once and
// asks the server; an answer of no, or no answer, puts it back and says so.
import type { QueryClient } from "@tanstack/react-query";
import { post } from "../../api/client";
import { go, type Route } from "../../app/route";
import { createStore } from "../../lib/store";
import { toast } from "../../lib/toast";
import type { ListData } from "./data";
import { filedWords, type Row } from "./rows";

export type Filing = "archive" | "trash" | "junk";
const WORDS: Record<Filing, { verb: string; act: string }> = {
  archive: { verb: "Archived", act: "archive" },
  trash: { verb: "Trashed", act: "delete" },
  junk: { verb: "Marked junk", act: "mark junk" },
};

// Rows on their way out, by pk. A refetch that lands meanwhile can't bring one back.
export const filing = createStore<ReadonlySet<number>>(new Set());
const hide = (pks: number[]) => filing.set(new Set([...filing.get(), ...pks]));
const unhide = (pks: number[]) => filing.set(new Set([...filing.get()].filter((pk) => !pks.includes(pk))));

export const undoFloat = createStore<string | null>(null);
let undoTimer: ReturnType<typeof setTimeout> | undefined;
function offerUndo(label: string) {
  undoFloat.set(label);
  clearTimeout(undoTimer);
  undoTimer = setTimeout(() => undoFloat.set(null), 8000);
}

export const readOnly = createStore(false);

function editRows(qc: QueryClient, edit: (rows: Row[]) => Row[]) {
  qc.setQueriesData<ListData>({ queryKey: ["messages"] }, (data) =>
    data && { ...data, pages: data.pages.map((page) => ({ ...page, rows: edit(page.rows) })) });
}

const patchRow = (qc: QueryClient, pk: number, change: Partial<Row>) =>
  editRows(qc, (rows) => rows.map((r) => (r.pk === pk ? { ...r, ...change } : r)));

let inFlight = 0;

export async function fileRow(qc: QueryClient, kind: Filing, row: Pick<Row, "pk">, whole: boolean, after: Route | null) {
  const { verb, act } = WORDS[kind];
  const path = whole && kind !== "junk" ? (`/api/thread/${kind}` as const) : (`/api/${kind}` as const);
  hide([row.pk]);
  if (after) go(after, { replace: true });
  inFlight++;
  try {
    const r = await post(path, { pk: row.pk });
    if (!r.ok) throw new Error("refused");
    const filed = "pks" in r && Array.isArray(r.pks) ? (r.pks as number[]) : [row.pk];
    const gone = new Set([row.pk, ...filed]);
    editRows(qc, (rows) => rows.filter((x) => !gone.has(x.pk)));
    const words = filedWords(verb, r as Parameters<typeof filedWords>[1], readOnly.get());
    toast(words);
    const n = (r as { count?: number }).count || 1;
    if ((r as { undoable?: boolean }).undoable) offerUndo(n > 1 ? `${verb} ${n}` : verb);
  } catch {
    toast(`Couldn’t ${act}`);
  } finally {
    unhide([row.pk]);
    if (--inFlight === 0) {
      void qc.invalidateQueries({ queryKey: ["messages"] });
      void qc.invalidateQueries({ queryKey: ["folders"] });
    }
  }
}

export async function undo(qc: QueryClient) {
  undoFloat.set(null);
  let d: { ok?: boolean; empty?: boolean; failed?: number; label?: string; error?: string };
  try {
    d = await post("/api/undo", {});
  } catch {
    toast("Couldn’t undo");
    return;
  }
  if (d.empty) toast("Nothing to undo");
  else if (!d.ok) toast(d.error || "Couldn’t undo");
  else if (d.failed) toast(`${d.failed} couldn’t be restored — press z to try again`);
  else toast(`${d.label || "Action"} undone`);
  void qc.invalidateQueries({ queryKey: ["messages"] });
  void qc.invalidateQueries({ queryKey: ["folders"] });
}

export async function setStar(qc: QueryClient, row: Pick<Row, "pk" | "flagged">) {
  const on = !row.flagged;
  patchRow(qc, row.pk, { flagged: on });
  try {
    const r = await post("/api/flag", { pk: row.pk, flag: "flagged", on });
    if (!r.ok) throw new Error("refused");
  } catch {
    patchRow(qc, row.pk, { flagged: !on });
    toast("Couldn’t change the star");
  }
}

// Opening a message marks it read after a moment on it, so running down a list with j leaves the rest unread.
const DWELL_MS = 1200;
let dwell: ReturnType<typeof setTimeout> | undefined;
export const cancelDwell = () => clearTimeout(dwell);

export async function markSeen(qc: QueryClient, pk: number, seen = true) {
  patchRow(qc, pk, { seen });
  try {
    const r = await post("/api/seen", { pk, seen });
    if (!r.ok) throw new Error("refused");
    void qc.invalidateQueries({ queryKey: ["folders"] });
    return true;
  } catch {
    patchRow(qc, pk, { seen: !seen });
    return false;
  }
}

export function armDwell(qc: QueryClient, pk: number) {
  cancelDwell();
  dwell = setTimeout(() => void markSeen(qc, pk), DWELL_MS);
}

export async function markUnread(qc: QueryClient, pk: number) {
  cancelDwell();
  toast((await markSeen(qc, pk, false)) ? "Marked unread" : "Couldn’t mark unread");
}

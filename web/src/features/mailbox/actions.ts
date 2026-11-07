// What you can do to a message from anywhere: the list, the reader, the keyboard. Filing hides the row at once and
// asks the server; an answer of no, or no answer, puts it back and says so.
import type { QueryClient } from "@tanstack/react-query";
import { post } from "../../lib/api/client";
import type { GetAnswers } from "../../lib/api/types.gen";
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

// The rows x has marked. With any marked, the verb keys act on the whole set.
export const marked = createStore<ReadonlySet<number>>(new Set());
export function toggleMark(pk: number) {
  const next = new Set(marked.get());
  if (!next.delete(pk)) next.add(pk);
  marked.set(next);
}
export const clearMarks = () => marked.set(new Set());

export const undoFloat = createStore<string | null>(null);
let undoTimer: ReturnType<typeof setTimeout> | undefined;
export function offerUndo(label: string) {
  undoFloat.set(label);
  clearTimeout(undoTimer);
  undoTimer = setTimeout(() => undoFloat.set(null), 8000);
}

export const readOnly = createStore(false);

// One id per bulk action, so one z reverses all of it. Unique across reloads: the undo stack outlives the page.
export const undoGroup = () => `ug-${crypto.randomUUID()}`;

function editRows(qc: QueryClient, edit: (rows: Row[]) => Row[]) {
  qc.setQueriesData<ListData>({ queryKey: ["messages"] }, (data) =>
    data && { ...data, pages: data.pages.map((page) => ({ ...page, rows: edit(page.rows) })) });
}

const patchRow = (qc: QueryClient, pk: number, change: Partial<Row>) =>
  editRows(qc, (rows) => rows.map((r) => (r.pk === pk ? { ...r, ...change } : r)));

let inFlight = 0;

export const readAgain = (qc: QueryClient) => {
  void qc.invalidateQueries({ queryKey: ["messages"] });
  void qc.invalidateQueries({ queryKey: ["folders"] });
  void qc.invalidateQueries({ queryKey: ["digest"] });
};

interface Answer { ok: boolean; error?: string; msg?: string; pks?: number[]; count?: number; failed?: number; status?: string; queued?: unknown; undoable?: boolean }

// Take rows off the list, ask the server, and say how it went. A no or no answer puts them back.
export async function act<R extends object>(qc: QueryClient, { rows, after, call, say, undoLabel, fail }: {
  rows: number[];
  after?: Route | null | undefined;
  call: () => Promise<R>;
  say: (r: R & Answer) => string;
  undoLabel?: (r: R & Answer) => string | null;
  fail: (reason: string) => string;
}) {
  hide(rows);
  if (after) go(after, { replace: true });
  inFlight++;
  try {
    const r = (await call()) as R & Answer;
    if (!r.ok) throw new Error(r.error || r.msg || "");
    const gone = new Set([...rows, ...(r.pks ?? [])]);
    editRows(qc, (list) => list.filter((x) => !gone.has(x.pk)));
    qc.setQueryData<GetAnswers["/api/digest"]>(["digest"], (d) => d && { ...d, items: d.items.filter((x) => !gone.has(x.pk)) });
    toast(say(r));
    const label = undoLabel?.(r);
    if (label) offerUndo(label);
  } catch (e) {
    toast(fail(e instanceof Error ? e.message : ""));
  } finally {
    unhide(rows);
    if (--inFlight === 0) readAgain(qc);
  }
}

export function fileRow(qc: QueryClient, kind: Filing, row: Pick<Row, "pk">, whole: boolean, after: Route | null) {
  const { verb, act: doing } = WORDS[kind];
  const path = whole && kind !== "junk" ? (`/api/thread/${kind}` as const) : (`/api/${kind}` as const);
  const n = (r: Answer) => r.count || 1;
  return act(qc, {
    rows: [row.pk],
    after,
    call: () => post(path, { pk: row.pk }),
    say: (r) => filedWords(verb, r, readOnly.get()),
    undoLabel: (r) => (r.undoable ? (n(r) > 1 ? `${verb} ${n(r)}` : verb) : null),
    fail: () => `Couldn’t ${doing}`,
  });
}

export type Target = { pk: number } | { account: string; thread_id: string } | { pks: number[] };

export type Snoozing = { preset: string } | { until: string };

export function snooze(qc: QueryClient, target: Target, when: Snoozing, rows: number[], after: Route | null) {
  return act(qc, {
    rows,
    after,
    call: () => post("/api/snooze", { ...target, ...when }),
    say: () => "Snoozed",
    undoLabel: () => "Snoozed",
    fail: (why) => `Couldn’t snooze${why ? `: ${why}` : ""}`,
  });
}

export function unsnooze(qc: QueryClient, target: Target, rows: number[], after: Route | null) {
  return act(qc, { rows, after, call: () => post("/api/unsnooze", target), say: () => "Unsnoozed", fail: () => "Couldn’t unsnooze" });
}

export function mute(qc: QueryClient, target: Target, rows: number[], after: Route | null) {
  return act(qc, {
    rows,
    after,
    call: () => post("/api/mute", target),
    say: () => "Muted",
    undoLabel: () => "Muted",
    fail: (why) => `Couldn’t mute${why ? `: ${why}` : ""}`,
  });
}

export function unmute(qc: QueryClient, target: Target, rows: number[], after: Route | null) {
  return act(qc, { rows, after, call: () => post("/api/unmute", target), say: () => "Unmuted", fail: () => "Couldn’t unmute" });
}

export function dismissFollowup(qc: QueryClient, target: Target, rows: number[], after: Route | null) {
  return act(qc, { rows, after, call: () => post("/api/followups/dismiss", target), say: () => "Dismissed", fail: () => "Couldn’t dismiss" });
}

// Reply later pins the thread in a stack of its own, and doesn't take it off the Inbox as snooze does.
export function replyLater(qc: QueryClient, target: Target, after: Route | null) {
  return act(qc, { rows: [], after, call: () => post("/api/replylater", { ...target, on: true }), say: () => "Saved to Reply later", fail: (why) => `Couldn’t save${why ? `: ${why}` : ""}` });
}

export function replyLaterDone(qc: QueryClient, target: Target, rows: number[], after: Route | null) {
  return act(qc, { rows, after, call: () => post("/api/replylater", { ...target, on: false }), say: () => "Done", fail: () => "Couldn’t update" });
}

export type BulkVerb = Filing | "read" | "unread" | "star" | "mute";

// What one request may file (`Filing.BULK_MAX`): more go as more requests, under one undo group.
const BULK_MAX = 500;

// A verb on every marked row, under one undo group: filing as one request, the rest one request each.
export async function bulk(qc: QueryClient, verb: BulkVerb, rows: Row[], isWhole: (r: Row) => boolean, after: Route | null) {
  clearMarks();
  const pks = rows.map((r) => r.pk);
  if (verb === "read" || verb === "unread" || verb === "star") {
    const change = verb === "star" ? { flagged: true } : { seen: verb === "read" };
    for (const pk of pks) patchRow(qc, pk, change);
    let failed = 0;
    for (const pk of pks) {
      try {
        const r: Answer = verb === "star"
          ? await post("/api/flag", { pk, flag: "flagged", on: true })
          : await post("/api/seen", { pk, seen: verb === "read" });
        if (!r.ok) failed++;
      } catch {
        failed++;
      }
    }
    toast(failed ? `Updated ${pks.length - failed}, ${failed} failed` : `Updated ${pks.length}`);
    readAgain(qc);
    return;
  }
  const group = undoGroup();
  if (verb === "mute") {
    const threads = new Map(rows.filter((r) => r.threadId).map((r) => [`${r.account}\0${r.threadId}`, r]));
    let done = 0;
    await act(qc, {
      rows: pks,
      after,
      call: async () => {
        for (const r of threads.values()) {
          try {
            if ((await post("/api/mute", { account: r.account, thread_id: r.threadId, undo_group: group })).ok) done++;
          } catch { /* counted by what's left */ }
        }
        return { ok: true };
      },
      say: () => `Muted ${done} thread${done === 1 ? "" : "s"}`,
      undoLabel: () => (done ? `Muted ${done}` : null),
      fail: () => "Couldn’t mute",
    });
    return;
  }
  const { verb: said } = WORDS[verb];
  let failed = 0, partial = false, undoable = false;
  await act(qc, {
    rows: pks,
    after,
    call: async () => {
      for (let at = 0; at < rows.length; at += BULK_MAX) {
        const chunk = rows.slice(at, at + BULK_MAX);
        try {
          const a = await post("/api/bulk/file", { verb, rows: chunk.map((r) => ({ pk: r.pk, whole: isWhole(r) })), undo_group: group });
          failed += a.rows_failed;
          partial ||= a.partial;
          undoable ||= Boolean(a.undoable);
        } catch {
          failed += chunk.length;
        }
      }
      return { ok: true };
    },
    say: () => (failed ? `${said} ${pks.length - failed}, ${failed} failed` : partial ? `${said} ${pks.length} — some messages stayed` : `${said} ${pks.length}`),
    undoLabel: () => (undoable ? `${said} ${pks.length - failed}` : null),
    fail: () => `Couldn’t ${WORDS[verb].act}`,
  });
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
  readAgain(qc);
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

// What you can do to a message from anywhere: the list, the reader, the keyboard. Filing hides the row at once and
// asks the server; an answer of no, or no answer, puts it back and says so.
import type { QueryClient } from "@tanstack/react-query";
import { get, post, refusalOf } from "../../lib/api/client";
import { LIMITS } from "../../lib/api/limits.gen";
import type { FilingVerb, GetAnswers, PostAnswers, SnoozePreset } from "../../lib/api/types";
import { go, parseRoute, pathOf, type Route } from "../../app/route";
import { createStore } from "../../lib/store";
import { copyId, copyOf, type Copy } from "../../lib/copy";
import { createFloat, OFFERS_MS, toast } from "../../lib/toast";
import type { ListData } from "./data";
import { filedWords, type Row } from "./rows";

export type Filing = FilingVerb;
const WORDS: Record<Filing, { verb: string; act: string }> = {
  archive: { verb: "Archived", act: "archive" },
  trash: { verb: "Trashed", act: "delete" },
  junk: { verb: "Marked junk", act: "mark junk" },
};

// A card on the brief or in Commitments has no list behind it to see the row leave, so it says where the mail went.
const FROM_A_CARD: Partial<Record<Filing, { said: string; undo: string }>> = {
  archive: { said: "Archived · in the mailbox's Archive", undo: "Archived" },
  trash: { said: "Deleted · in the mailbox's Trash", undo: "Deleted" },
};

// Rows on their way out, by copy (`copyId`). A refetch that lands meanwhile can't bring one back.
export const filing = createStore<ReadonlySet<string>>(new Set());
const hide = (ids: string[]) => filing.set(new Set([...filing.get(), ...ids]));
const unhide = (ids: string[]) => filing.set(new Set([...filing.get()].filter((id) => !ids.includes(id))));

// The rows x has marked, by copy. With any marked, the verb keys act on the whole set.
export const marked = createStore<ReadonlySet<string>>(new Set());
export function toggleMark(id: string) {
  const next = new Set(marked.get());
  if (!next.delete(id)) next.add(id);
  marked.set(next);
}
export const clearMarks = () => marked.set(new Set());

export const undoFloat = createFloat(OFFERS_MS);
// A toast has just told a screen reader what happened: this adds what can be done about it.
export const offerUndo = (label: string) => undoFloat.show(label, "Undo is available");

export const readOnly = createStore(false);

// What a move to a folder is about: whose folders it may go to, and the one folder it is in, which it isn't offered.
export interface MoveAsk { account: string; from: string | null }
export const moveAsk = createStore<MoveAsk | null>(null);

// One id per bulk action, so one z reverses all of it. Unique across reloads: the undo stack outlives the page.
export const undoGroup = () => `ug-${crypto.randomUUID()}`;

function editRows(qc: QueryClient, edit: (rows: Row[]) => Row[]) {
  qc.setQueriesData<ListData>({ queryKey: ["messages"] }, (data) =>
    data && { ...data, pages: data.pages.map((page) => ({ ...page, rows: edit(page.rows) })) });
}

const patchRow = (qc: QueryClient, copy: Copy, change: Partial<Row>) =>
  editRows(qc, (rows) => rows.map((r) => (r.id === copyId(copy) ? { ...r, ...change } : r)));

let inFlight = 0;

export const readAgain = (qc: QueryClient) => {
  void qc.invalidateQueries({ queryKey: ["messages"] });
  void qc.invalidateQueries({ queryKey: ["folders"] });
  void qc.invalidateQueries({ queryKey: ["digest"] });
};

interface Answer { ok: true; msg?: string; filed?: Copy[]; count?: number; failed?: number; status?: string | null; queued?: unknown; undoable?: boolean | null }

// Take rows off the list, ask the server, and say how it went. A no or no answer puts them back. `rows` are copies, by `copyId`.
export async function act<R extends object>(qc: QueryClient, { rows, after, call, say, undoLabel, fail }: {
  rows: string[];
  after?: Route | null | undefined;
  call: () => Promise<R>;
  say: (r: R & Answer) => string;
  undoLabel?: (r: R & Answer) => string | null;
  fail: (reason: string) => string;
}) {
  hide(rows);
  const was = parseRoute(location.pathname, location.search);
  if (after) go(after, { replace: true });
  inFlight++;
  try {
    const r = (await call()) as R & Answer;
    const gone = new Set([...rows, ...(r.filed ?? []).map(copyId)]);
    editRows(qc, (list) => list.filter((x) => !gone.has(x.id)));
    qc.setQueryData<GetAnswers["/api/digest"]>(["digest"], (d) => d && { ...d, items: d.items.filter((x) => !gone.has(copyId(x))) });
    toast(say(r));
    const label = undoLabel?.(r);
    if (label) offerUndo(label);
  } catch (e) {
    toast(fail(e instanceof Error ? e.message : ""));
    if (after && location.pathname + location.search === pathOf(after)) go(was, { replace: true });   // it stayed, so back to it
  } finally {
    unhide(rows);
    if (--inFlight === 0) readAgain(qc);
  }
}

export function fileRow(qc: QueryClient, kind: Filing, row: Copy, whole: boolean, after: Route | null, from?: "card") {
  const { verb, act: doing } = WORDS[kind];
  const card = from === "card" ? FROM_A_CARD[kind] : undefined;
  const path = whole && kind !== "junk" ? (`/api/thread/${kind}` as const) : (`/api/${kind}` as const);
  const n = (r: Answer) => r.count || 1;
  return act(qc, {
    rows: [copyId(row)],
    after,
    call: () => post(path, copyOf(row)),
    say: (r) => {
      const said = filedWords(verb, r, readOnly.get());
      return card && said === verb ? card.said : said;
    },
    undoLabel: (r) => (r.undoable ? (n(r) > 1 ? `${verb} ${n(r)}` : card?.undo ?? verb) : null),
    fail: () => `Couldn’t ${doing}`,
  });
}

// One message, or the conversation its row stands for, to a folder of its account's that a person picked.
export function moveRow(qc: QueryClient, to: string, label: string, row: Copy, whole: boolean, after: Route | null) {
  const n = (r: Answer) => r.count || 1;
  return act(qc, {
    rows: [copyId(row)],
    after,
    call: () => post(whole ? "/api/thread/move" : "/api/move", { ...copyOf(row), to }),
    say: (r) => filedWords("Moved", r, readOnly.get(), label),
    undoLabel: (r) => (r.undoable ? (n(r) > 1 ? `Moved ${n(r)}` : "Moved") : null),
    fail: (why) => `Couldn’t move${why ? `: ${why}` : ""}`,
  });
}

// What a verb is about: a copy, a whole thread, or the copies x marked. A copy may say its thread, for a verb on the thread.
// One message or one conversation, as a route that acts on the message takes it. Only a snooze also takes `copies`.
export type OneTarget = (Copy & { thread_id?: string | null }) | { account: string; thread_id: string };
export type Target = OneTarget | { copies: Copy[] };

export type Snoozing = { preset: SnoozePreset } | { until: string };

// Marked copies go as many requests as the server's limit on one asks, under one undo group, as filing's do: one z brings
// them all back. A request that fails says how many stayed; when none went, it says why, as one request would.
export function snooze(qc: QueryClient, target: Target, when: Snoozing, rows: string[], after: Route | null) {
  const fail = (why: string) => `Couldn’t snooze${why ? `: ${why}` : ""}`;
  if (!("copies" in target)) {
    return act(qc, { rows, after, call: () => post("/api/snooze", { ...target, ...when }), say: () => "Snoozed", undoLabel: () => "Snoozed", fail });
  }
  const { copies } = target, group = undoGroup();
  let failed = 0;
  return act(qc, {
    rows,
    after,
    call: async () => {
      let first: unknown;
      for (let at = 0; at < copies.length; at += LIMITS.snooze_copies) {
        const chunk = copies.slice(at, at + LIMITS.snooze_copies);
        try {
          await post("/api/snooze", { copies: chunk, ...when, undo_group: group });
        } catch (e) {
          first ??= e;
          failed += chunk.length;
        }
      }
      if (first !== undefined && failed === copies.length) throw first;
      return { ok: true };
    },
    say: () => (failed ? `Snoozed ${copies.length - failed}, ${failed} failed` : "Snoozed"),
    undoLabel: () => "Snoozed",
    fail,
  });
}

export function unsnooze(qc: QueryClient, target: Target, rows: string[], after: Route | null) {
  return act(qc, { rows, after, call: () => post("/api/unsnooze", target), say: () => "Unsnoozed", fail: () => "Couldn’t unsnooze" });
}

export function mute(qc: QueryClient, target: OneTarget, rows: string[], after: Route | null) {
  return act(qc, {
    rows,
    after,
    call: () => post("/api/mute", target),
    say: () => "Muted",
    undoLabel: () => "Muted",
    fail: (why) => `Couldn’t mute${why ? `: ${why}` : ""}`,
  });
}

export function unmute(qc: QueryClient, target: OneTarget, rows: string[], after: Route | null) {
  return act(qc, { rows, after, call: () => post("/api/unmute", target), say: () => "Unmuted", fail: () => "Couldn’t unmute" });
}

export function dismissFollowup(qc: QueryClient, target: OneTarget, rows: string[], after: Route | null) {
  return act(qc, { rows, after, call: () => post("/api/followups/dismiss", target), say: () => "Dismissed", fail: () => "Couldn’t dismiss" });
}

// Reply later pins the thread in a stack of its own, and doesn't take it off the Inbox as snooze does.
export function replyLater(qc: QueryClient, target: Target, after: Route | null) {
  return act(qc, { rows: [], after, call: () => post("/api/replylater", { ...target, on: true }), say: () => "Saved to Reply later", fail: (why) => `Couldn’t save${why ? `: ${why}` : ""}` });
}

export function replyLaterDone(qc: QueryClient, target: Target, rows: string[], after: Route | null) {
  return act(qc, { rows, after, call: () => post("/api/replylater", { ...target, on: false }), say: () => "Done", fail: () => "Couldn’t update" });
}

export type BulkVerb = Filing | "read" | "unread" | "star" | "mute";

// A verb on every marked row, under one undo group: filing as one request, the rest one request each.
export async function bulk(qc: QueryClient, verb: BulkVerb, rows: Row[], isWhole: (r: Row) => boolean, after: Route | null) {
  clearMarks();
  const ids = rows.map((r) => r.id);
  if (verb === "read" || verb === "unread" || verb === "star") {
    const change = verb === "star" ? { flagged: true } : { seen: verb === "read" };
    for (const r of rows) patchRow(qc, r, change);
    let failed = 0;
    for (const r of rows) {
      try {
        if (verb === "star") await post("/api/flag", { ...copyOf(r), flag: "flagged", on: true });
        else await post("/api/seen", { ...copyOf(r), seen: verb === "read" });
      } catch {
        failed++;
      }
    }
    toast(failed ? `Updated ${rows.length - failed}, ${failed} failed` : `Updated ${rows.length}`);
    readAgain(qc);
    return;
  }
  const group = undoGroup();
  if (verb === "mute") {
    const threads = new Map(rows.filter((r) => r.threadId).map((r) => [`${r.account}\0${r.threadId}`, r]));
    let done = 0;
    await act(qc, {
      rows: ids,
      after,
      call: async () => {
        for (const r of threads.values()) {
          try {
            await post("/api/mute", { account: r.account, thread_id: r.threadId, undo_group: group });
            done++;
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
  const said = (n: number) => `${WORDS[verb].verb} ${n}`;
  await inChunks(qc, rows, after, { said, undo: said, couldnt: `Couldn’t ${WORDS[verb].act}` }, (chunk) =>
    post("/api/bulk/file", { verb, rows: chunk.map((r) => ({ ...copyOf(r), whole: isWhole(r) })), undo_group: group }));
}

// Marked rows of one account to a folder of its own that a person picked.
export async function moveMarked(qc: QueryClient, to: string, label: string, rows: Row[], isWhole: (r: Row) => boolean, after: Route | null) {
  clearMarks();
  const group = undoGroup();
  await inChunks(qc, rows, after, { said: (n) => `Moved ${n} to ${label}`, undo: (n) => `Moved ${n}`, couldnt: "Couldn’t move" }, (chunk) =>
    post("/api/bulk/move", { to, rows: chunk.map((r) => ({ ...copyOf(r), whole: isWhole(r) })), undo_group: group }));
}

// As many requests as what one may file allows, each saying how its rows went. A request that fails counts all of its rows.
async function inChunks(qc: QueryClient, rows: Row[], after: Route | null, words: { said: (n: number) => string; undo: (n: number) => string; couldnt: string }, send: (chunk: Row[]) => Promise<PostAnswers["/api/bulk/file"]>) {
  const ids = rows.map((r) => r.id);
  let failed = 0, partial = false, undoable = false;
  await act(qc, {
    rows: ids,
    after,
    call: async () => {
      for (let at = 0; at < rows.length; at += LIMITS.bulk_rows) {
        const chunk = rows.slice(at, at + LIMITS.bulk_rows);
        try {
          const a = await send(chunk);
          failed += a.rows_failed;
          partial ||= a.partial;
          undoable ||= Boolean(a.undoable);
        } catch {
          failed += chunk.length;
        }
      }
      return { ok: true };
    },
    say: () => (failed ? `${words.said(ids.length - failed)}, ${failed} failed` : partial ? `${words.said(ids.length)} — some messages stayed` : words.said(ids.length)),
    undoLabel: () => (undoable ? words.undo(ids.length - failed) : null),
    fail: () => words.couldnt,
  });
}

export async function undo(qc: QueryClient) {
  undoFloat.clear();
  let d: PostAnswers["/api/undo"];
  try {
    d = await post("/api/undo", {});
  } catch (e) {
    const no = refusalOf(e);
    if (!no) return toast("Couldn’t undo");
    toast(no.problem["empty"] ? "Nothing to undo" : no.detail || "Couldn’t undo");
    readAgain(qc);
    return;
  }
  if (d.failed) toast(`${d.failed} couldn’t be restored — press z to try again`);
  else toast(`${d.label || "Action"} undone`);
  // A move's Undo gives each copy a new uid, so a conversation held from before names copies that are gone.
  qc.removeQueries({ queryKey: ["thread"] });
  readAgain(qc);
}

// A mark shown on the row at once and then asked of the server, for each of `copies`. A no puts the row back.
async function markRow(qc: QueryClient, row: Copy, shows: Partial<Row>, was: Partial<Row>, copies: () => Promise<Copy[]> | Copy[], send: (c: Copy) => Promise<unknown>, said: string, couldnt: string) {
  patchRow(qc, row, shows);
  try {
    for (const c of await copies()) await send(c);
    void qc.invalidateQueries({ queryKey: ["folders"] });
    toast(said);
  } catch {
    patchRow(qc, row, was);
    toast(couldnt);
  }
}

export function setStar(qc: QueryClient, row: Copy & Pick<Row, "flagged">, copies: () => Promise<Copy[]> | Copy[] = () => [row]) {
  const on = !row.flagged;
  return markRow(qc, row, { flagged: on }, { flagged: !on }, copies, (c) => post("/api/flag", { ...copyOf(c), flag: "flagged", on }), on ? "Starred" : "Unstarred", "Couldn’t star");
}

// A conversation's row sums its members: it reads as read when none is unread, and as starred when any is. So marking it
// read or unstarring it takes every member that makes it so; the other way round, the copy the row names does.
const membersOf = async (row: Row) => (await get(`/api/thread?account=${encodeURIComponent(row.account)}&thread_id=${encodeURIComponent(row.threadId)}`)).items;

export function starRow(qc: QueryClient, row: Row, whole: boolean) {
  return setStar(qc, row, whole && row.flagged ? async () => (await membersOf(row)).filter((m) => m.flagged) : undefined);
}

// Opening a message marks it read after a moment on it, so running down a list with j leaves the rest unread.
const DWELL_MS = 1200;
let dwell: ReturnType<typeof setTimeout> | undefined;
export const cancelDwell = () => clearTimeout(dwell);

export async function markSeen(qc: QueryClient, copy: Copy, seen = true) {
  patchRow(qc, copy, { seen });
  try {
    await post("/api/seen", { ...copyOf(copy), seen });
    void qc.invalidateQueries({ queryKey: ["folders"] });
    return true;
  } catch {
    patchRow(qc, copy, { seen: !seen });
    return false;
  }
}

export function armDwell(qc: QueryClient, copy: Copy) {
  cancelDwell();
  dwell = setTimeout(() => void markSeen(qc, copy), DWELL_MS);
}

export async function markUnread(qc: QueryClient, copy: Copy) {
  cancelDwell();
  toast((await markSeen(qc, copy, false)) ? "Marked unread" : "Couldn’t update");
}

// The row's own Mark read and Mark unread. On the open row the dwell mustn't then take it back.
export function seenRow(qc: QueryClient, row: Row, whole: boolean, open: boolean) {
  if (open) cancelDwell();
  const seen = !row.seen;
  return markRow(qc, row, { seen }, { seen: !seen }, whole && seen ? async () => (await membersOf(row)).filter((m) => !m.seen) : () => [row], (c) => post("/api/seen", { ...copyOf(c), seen }), seen ? "Marked read" : "Marked unread", "Couldn’t update");
}

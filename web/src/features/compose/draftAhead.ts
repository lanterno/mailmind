// A reply written ahead, for mail that wants one. Opening a message peeks, which never spends; staying on it a moment asks
// for one, and the event stream says when it's ready. Nothing goes on while you're writing. A reply is to a message, so a chip
// is kept by the message's key, and asked for by the copy on screen.
import { useEffect } from "react";
import { get } from "../../lib/api/client";
import { copyQuery, type Copy } from "../../lib/copy";
import { createStore } from "../../lib/store";
import { announce } from "../../lib/toast";
import { composing, onForgetDraftAhead } from "./compose";

const DWELL_MS = 1200;
const LIVE_TRIES = 2, LIVE_WAIT_MS = 10000;

type Status = "ready" | "drafting";
const chips = createStore<ReadonlyMap<number, Status>>(new Map());
let viewing: Copy | null = null;
let dwell: ReturnType<typeof setTimeout> | undefined;

function mark(key: number, status: Status | null) {
  const next = new Map(chips.get());
  if (status) next.set(key, status);
  else next.delete(key);
  chips.set(next);
}
export const forget = (key: number) => mark(key, null);
onForgetDraftAhead(forget);

const onScreen = (key: number) => viewing?.key === key && !composing.isOpen();

async function peek(copy: Copy) {
  let d;
  try {
    d = await get(`/api/ai/draft-ahead?${copyQuery(copy)}&peek=1`);
  } catch {
    return;
  }
  if (d.status === "ready") mark(copy.key, "ready");
  else if (d.status === "generating" && chips.get().get(copy.key) !== "drafting") {   // one poll chain per message
    mark(copy.key, "drafting");
    void check(copy, 0);
  }
}

async function check(copy: Copy, tries: number) {
  const { key } = copy;
  if (!onScreen(key) || chips.get().get(key) === "ready") return;
  let d;
  try {
    d = await get(`/api/ai/draft-ahead?${copyQuery(copy)}`);
  } catch {
    return;
  }
  if (d.status === "ready" && d.body) {
    const first = chips.get().get(key) !== "ready";
    mark(key, "ready");
    if (first && onScreen(key)) announce("AI reply draft ready — press r to review");
  } else if (d.status === "generating") {
    if (tries < LIVE_TRIES && onScreen(key)) {
      mark(key, "drafting");
      setTimeout(() => void check(copy, tries + 1), LIVE_WAIT_MS);   // the stream says when; this is only a net for a dropped event
    } else forget(key);   // gave up or moved off: never strand "Drafting…"
  } else forget(key);
}

// The event stream's "draft-ready", by the key of the message it answers: read again, never trust what it says.
export function onDraftReady(key: number | null | undefined) {
  if (key === null || key === undefined || document.hidden) return;
  if (viewing?.key === key) void check(viewing, LIVE_TRIES);
  else if (viewing !== null && !composing.isOpen() && !chips.get().has(viewing.key)) void peek(viewing);   // a sibling in the open thread may have got it
}

export function useDraftAhead(copy: Copy | null, wanted: boolean, skip: boolean) {
  const key = copy?.key ?? null, folder = copy?.folder ?? "";
  useEffect(() => {
    if (key === null || skip) return;
    const here = { key, folder };
    viewing = here;
    if (chips.get().get(key) !== "ready") {
      void peek(here);
      if (wanted) {
        dwell = setTimeout(() => {   // not on a fast skim
          if (onScreen(key) && chips.get().get(key) !== "drafting") void check(here, 0);
        }, DWELL_MS);
      }
    }
    return () => {
      clearTimeout(dwell);
      viewing = null;
    };
  }, [key, folder, wanted, skip]);
}

// Which of the brief's cards already have a reply written: peeks only, once a message.
const peeked = new Set<number>();
export async function peekCards(copies: Copy[]) {
  const fresh = copies.filter((c) => !peeked.has(c.key) && chips.get().get(c.key) !== "ready");
  fresh.forEach((c) => peeked.add(c.key));
  await Promise.all(fresh.map(async (c) => {
    try {
      const d = await get(`/api/ai/draft-ahead?${copyQuery(c)}&peek=1`);
      if (d.status === "ready") mark(c.key, "ready");
    } catch { /* it stays a plain Reply */ }
  }));
}

// r, and the Reply button: the draft written ahead if there is one, else a plain reply.
export const answer = (to: Copy) => (chips.get().get(to.key) === "ready" ? composing.openDraftAhead(to, () => forget(to.key)) : composing.reply(to));

// A message's chip, by its key.
export const useDraftChip = (key: number | null) => {
  const all = chips.useValue();
  return key === null ? undefined : all.get(key);
};

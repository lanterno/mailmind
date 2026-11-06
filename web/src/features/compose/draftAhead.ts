// A reply written ahead, for mail that wants one. Opening a message peeks, which never spends; staying on it a moment asks
// for one, and the event stream says when it's ready. Nothing goes on while you're writing.
import { useEffect } from "react";
import { get } from "../../api/client";
import { createStore } from "../../lib/store";
import { announce } from "../../lib/toast";
import { composing, onForgetDraftAhead } from "./compose";

const DWELL_MS = 1200;
const LIVE_TRIES = 2, LIVE_WAIT_MS = 10000;

type Status = "ready" | "drafting";
const chips = createStore<ReadonlyMap<number, Status>>(new Map());
let viewing: number | null = null;
let dwell: ReturnType<typeof setTimeout> | undefined;

function mark(pk: number, status: Status | null) {
  const next = new Map(chips.get());
  if (status) next.set(pk, status);
  else next.delete(pk);
  chips.set(next);
}
export const forget = (pk: number) => mark(pk, null);
onForgetDraftAhead(forget);

const onScreen = (pk: number) => viewing === pk && !composing.isOpen();

async function peek(pk: number) {
  let d;
  try {
    d = await get(`/api/ai/draft-ahead?pk=${pk}&peek=1`);
  } catch {
    return;
  }
  if (!d?.ok) return;
  if (d.status === "ready") mark(pk, "ready");
  else if (d.status === "generating" && chips.get().get(pk) !== "drafting") {   // one poll chain per message
    mark(pk, "drafting");
    void check(pk, 0);
  }
}

async function check(pk: number, tries: number) {
  if (!onScreen(pk) || chips.get().get(pk) === "ready") return;
  let d;
  try {
    d = await get(`/api/ai/draft-ahead?pk=${pk}`);
  } catch {
    return;
  }
  if (!d?.ok) return;
  if (d.status === "ready" && d.body) {
    const first = chips.get().get(pk) !== "ready";
    mark(pk, "ready");
    if (first && onScreen(pk)) announce("AI reply draft ready — press r to review");
  } else if (d.status === "generating") {
    if (tries < LIVE_TRIES && onScreen(pk)) {
      mark(pk, "drafting");
      setTimeout(() => void check(pk, tries + 1), LIVE_WAIT_MS);   // the stream says when; this is only a net for a dropped event
    } else forget(pk);   // gave up or moved off: never strand "Drafting…"
  } else forget(pk);
}

// The event stream's "draft-ready", ids only: read again, never trust what it says.
export function onDraftReady(pk: number | null | undefined) {
  if (pk === null || pk === undefined || document.hidden) return;
  if (viewing === pk) void check(pk, LIVE_TRIES);
  else if (viewing !== null && !composing.isOpen() && !chips.get().has(viewing)) void peek(viewing);   // a sibling in the open thread may have got it
}

export function useDraftAhead(pk: number | null, wanted: boolean, skip: boolean) {
  useEffect(() => {
    if (pk === null || skip) return;
    viewing = pk;
    if (chips.get().get(pk) !== "ready") {
      void peek(pk);
      if (wanted) {
        dwell = setTimeout(() => {   // not on a fast skim
          if (onScreen(pk) && chips.get().get(pk) !== "drafting") void check(pk, 0);
        }, DWELL_MS);
      }
    }
    return () => {
      clearTimeout(dwell);
      viewing = null;
    };
  }, [pk, wanted, skip]);
}

// Which of the brief's cards already have a reply written: peeks only, once a card.
const peeked = new Set<number>();
export async function peekCards(pks: number[]) {
  const fresh = pks.filter((pk) => !peeked.has(pk) && chips.get().get(pk) !== "ready");
  fresh.forEach((pk) => peeked.add(pk));
  await Promise.all(fresh.map(async (pk) => {
    try {
      const d = await get(`/api/ai/draft-ahead?pk=${pk}&peek=1`);
      if (d?.ok && d.status === "ready") mark(pk, "ready");
    } catch { /* it stays a plain Reply */ }
  }));
}

// r, and the Reply button: the draft written ahead if there is one, else a plain reply.
export const answer = (pk: number) => (chips.get().get(pk) === "ready" ? composing.openDraftAhead(pk, () => forget(pk)) : composing.reply(pk));

export const useDraftChip = (pk: number | null) => {
  const all = chips.useValue();
  return pk === null ? undefined : all.get(pk);
};

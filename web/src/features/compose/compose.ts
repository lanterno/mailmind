// The app's one composer: the machine, on this page's server, storage and DOM.
import type { QueryClient } from "@tanstack/react-query";
import { get, post } from "../../lib/api/client";
import { LIMITS } from "../../lib/api/limits.gen";
import type { DraftTone, GetAnswers } from "../../lib/api/types";
import { go, parseRoute } from "../../app/route";
import { askOverLimit } from "../../lib/overLimit";
import { createStore, store, stored } from "../../lib/store";
import { announce, toast } from "../../lib/toast";
import { createComposing, type Focus } from "./machine";
import { isDraftTone } from "./model";
import { mineSet } from "./sendGuard";

let qc: QueryClient | undefined;
export const bindComposer = (client: QueryClient) => void (qc = client);

const digest = () => qc?.getQueryData<GetAnswers["/api/digest"]>(["digest"]);
const route = () => parseRoute(location.pathname, location.search);
const again = (...keys: string[]) => keys.forEach((key) => void qc?.invalidateQueries({ queryKey: [key] }));

export function myAddresses(d = digest()): Set<string> {
  return mineSet([...(d?.send_accounts ?? []), ...(d?.accounts ?? [])]);
}

const savedTone = stored("mm_draft_tone");
export const draftTone = createStore<DraftTone>(isDraftTone(savedTone) ? savedTone : "balanced");
export function setDraftTone(tone: DraftTone) {
  draftTone.set(tone);
  store("mm_draft_tone", tone);
}

const fieldInput = (field: string) => document.querySelector<HTMLInputElement>(`#cmp${field[0]!.toUpperCase()}${field.slice(1)} .ci`);
const bodyBox = () => document.querySelector<HTMLTextAreaElement>("#cmpBody");

function elementOf(what: Focus): HTMLElement | null {
  if (typeof what === "object") return fieldInput(what.field);
  return document.querySelector<HTMLElement>({ guard: "#cmpGuard", send: "#cmpSend", body: "#cmpBody", subject: "#cmpSubject", schedule: "#cmpSchedule", attach: "#cmpAttachBtn" }[what]);
}
// What the machine just changed is on screen after React's turn.
const afterRender = (fn: () => void) => void setTimeout(fn);

export const composing = createComposing({
  createStore,
  post: (path, body, ai) => post(path as "/api/draft", body, ai ? { askOverLimit } : {}),
  get: (path) => get(path as "/api/digest"),
  toast,
  say: announce,
  senders: () => {
    const d = digest();
    return d?.send_accounts?.length ? d.send_accounts : d?.accounts ?? [];
  },
  current: () => {
    const { source } = route();
    return source?.kind === "folder" || source?.kind === "search" ? source.account : undefined;
  },
  mine: () => myAddresses(),
  signature: (email) => {
    const d = digest();
    return d?.signatures?.[email] || d?.signature_default || "";
  },
  tone: draftTone.get,
  limits: LIMITS,
  focusHere: () => {   // a chip's input by its field: chips come and go around it
    const el = document.activeElement as HTMLElement | null;
    if (!el?.closest("#composeModal") || el.closest("#cmpGuard")) return null;
    return el.classList.contains("ci") ? { field: el.dataset["cf"] } : el;
  },
  focusBack: (where, fallback) => afterRender(() => {
    const active = document.activeElement;
    if (active && active !== document.body && !active.closest("#cmpGuard") && active.closest("#composeModal")) return;   // moved elsewhere meanwhile: stay there
    const field = (where as { field?: string } | null)?.field;
    const el = field ? fieldInput(field) : where instanceof HTMLElement && where.isConnected ? where : elementOf(fallback);
    el?.focus();
  }),
  focus: (what) => afterRender(() => elementOf(what)?.focus()),
  pickFile: () => document.querySelector<HTMLInputElement>("#cmpFile")?.click(),
  readFile: (file) => new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(String(r.result).split(",")[1] ?? "");
    r.onerror = r.onabort = () => reject(r.error ?? new Error("read"));
    r.readAsDataURL(file);
  }),
  prompt: (question, fallback) => window.prompt(question, fallback),
  selection: () => {
    const box = bodyBox();
    return box ? [box.selectionStart, box.selectionEnd] : [0, 0];
  },
  select: (start, end) => afterRender(() => bodyBox()?.setSelectionRange(start, end)),
  draftsChanged: () => again("messages", "folders"),
  foldersChanged: () => again("folders"),
  outboxChanged: () => again("outbox", "pending"),
  sent: (replyKey) => {
    again("messages", "thread", "folders");
    if (replyKey !== null) forgetDraftAhead(replyKey);
  },
  draftGone: (copy) => {
    const here = route();
    if (here.opened && here.opened.key === copy.key && here.opened.folder === copy.folder) go({ source: here.source, opened: null });
  },
  now: () => Date.now() / 1000,
  clock: () => performance.now(),
});

// A reply sent: its draft written ahead must not be offered again. draftAhead.ts holds those by the message's key, and says how
// to forget one here.
let forgetDraftAhead: (key: number) => void = () => {};
export const onForgetDraftAhead = (fn: (key: number) => void) => void (forgetDraftAhead = fn);

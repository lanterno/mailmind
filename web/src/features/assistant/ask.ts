// Asking and doing, from the search box: "?…" asks your inbox and changes nothing, "!…" plans a bulk action. What a plan
// would touch is frozen by the server at the preview, and nothing happens until you confirm it, by the plan's id.
import type { QueryClient } from "@tanstack/react-query";
import { ApiError, post } from "../../lib/api/client";
import type { PlanVerb, PostAnswers } from "../../lib/api/types";
import { readAgain, offerUndo, undoGroup } from "../mailbox";
import { askOverLimit } from "../../lib/overLimit";
import { createStore } from "../../lib/store";
import { announce, toast } from "../../lib/toast";

export type Answer = PostAnswers["/api/ai/ask"];
export type Plan = PostAnswers["/api/ai/do-plan"];

export type Asking =
  | { question: string; state: "loading" }
  | { question: string; state: "error"; error: string }
  | { question: string; state: "answer"; answer: Answer };

export const asking = createStore<Asking | null>(null);
let askSeq = 0;

export async function ask(question: string, account = "all") {
  const seq = ++askSeq;
  asking.set({ question, state: "loading" });
  try {
    const d = await post("/api/ai/ask", { account, question }, { askOverLimit });
    if (seq !== askSeq) return;      // a newer one took its place
    asking.set({ question, state: "answer", answer: d });
    const n = d.sources?.length ?? 0;
    announce(`Answer ready — ${n} cited source${n === 1 ? "" : "s"}`);
  } catch (e) {
    if (seq === askSeq) asking.set({ question, state: "error", error: e instanceof ApiError && e.aiOff ? "AI is off" : "ask failed" });
  }
}

export function stopAsking() {
  askSeq++;
  if (asking.get()) asking.set(null);
}

export const VERB_LABEL: Record<PlanVerb, string> = { archive: "Archive", trash: "Delete", junk: "Mark junk", read: "Mark read", unread: "Mark unread", star: "Star", snooze: "Snooze" };
const VERB_DONE: Partial<Record<PlanVerb, string>> = { archive: "Archived", trash: "Trashed", junk: "Marked junk", snooze: "Snoozed" };

export type Doing =
  | { state: "intro" }
  | { state: "loading"; cmd: string }
  | { state: "error"; cmd: string; error: string }
  | { state: "nofilter"; cmd: string }
  | { state: "plan"; cmd: string; plan: Extract<Plan, { intent: "act" }>; armed: boolean };

export const doing = createStore<Doing | null>(null);
let doSeq = 0;

// A plan that isn't an action is a search: it acts on nothing. `search` is how the caller shows one.
export async function runDo(cmd: string, search: (q: string) => void) {
  const seq = ++doSeq;
  doing.set({ state: "loading", cmd });
  let d: Plan;
  try {
    d = await post("/api/ai/do-plan", { cmd }, { askOverLimit });
  } catch (e) {
    if (seq === doSeq) doing.set({ state: "error", cmd, error: e instanceof ApiError && e.aiOff ? "AI is off" : "planner failed" });
    return;
  }
  if (seq !== doSeq) return;
  if (d.intent !== "act") {
    const q = (d.filter || "").trim();
    if (!q) return doing.set({ state: "nofilter", cmd });
    doing.set(null);
    return search(q);
  }
  doing.set({ state: "plan", cmd, plan: d, armed: false });
  announce(`${VERB_LABEL[d.verb]} ${d.count} messages? Review and confirm.`);
}

export function stopDoing() {
  doSeq++;
  doing.set(null);
}

// A search box's sentence, turned into a search by the planner a bulk command uses. Only ever from Enter (the box asks
// `readsAsProse` first), and only with the literal search already on screen: a call that fails, is off, or is over the day's limit
// leaves that standing, and asks nobody anything. A plan's verb goes unread, so a sentence that reads like a command is a search.
export const translating = createStore<string | null>(null);
let translateSeq = 0;
let lastTranslation: { prose: string; filter: string } | null = null;     // pressing Enter again on the same words costs nothing
const keptLiteral = new Set<string>();                                    // whose translation someone took back

export async function translateProse(prose: string, show: (filter: string) => void) {
  if (keptLiteral.has(prose) || translating.get() === prose) return;
  if (lastTranslation?.prose === prose) return show(lastTranslation.filter);
  const seq = ++translateSeq;
  translating.set(prose);
  let filter: string;
  try {
    filter = ((await post("/api/ai/do-plan", { cmd: prose })).filter ?? "").trim();
  } catch {
    filter = "";
  }
  if (seq !== translateSeq) return;
  translating.set(null);
  if (!filter || filter.toLowerCase() === prose.toLowerCase()) return;
  lastTranslation = { prose, filter };
  show(filter);
}

export function stopTranslating() {
  translateSeq++;
  translating.set(null);
}

export function keepLiteral(prose: string) {
  keptLiteral.add(prose);
  if (lastTranslation?.prose === prose) lastTranslation = null;
}

export async function confirmPlan(qc: QueryClient, plan: Plan, clearBox: () => void) {
  if (plan.intent !== "act" || !plan.plan) return;
  stopDoing();
  const verb = plan.verb;
  toast(`${VERB_LABEL[verb]} ${plan.capped ? plan.cap : plan.count}…`);
  try {
    const r = await post("/api/ai/do-confirm", { plan: plan.plan, undo_group: undoGroup() });
    const done = VERB_DONE[verb] || "Updated";
    toast([`${done} ${r.count}`, ...(r.failed ? [`${r.failed} failed`] : []), ...(r.gone ? [`${r.gone} had moved`] : [])].join(", "));
    if (r.undoable) offerUndo(`${done} ${r.count}`);
  } catch (e) {
    toast(e instanceof ApiError && e.status === 410 ? "That preview has expired. Run the command again." : "Couldn’t finish that. Check what was done before trying again.");
  }
  clearBox();
  readAgain(qc);
}

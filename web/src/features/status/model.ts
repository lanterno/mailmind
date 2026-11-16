// What the digest says about syncing and each account, in words. No DOM.
import type { GetAnswers } from "../../lib/api/types";
import { shortAddress } from "../../lib/text";

export type Digest = GetAnswers["/api/digest"];
// The generated type sees only what the tests' worlds answered.
type Health = Record<string, { health?: string; done?: number | null; total?: number | null } | undefined>;
interface SyncState { last: string | null; result?: string; running?: boolean }

export const nameOf = (digest: Digest | undefined, a: string) => digest?.aliases?.[a] || shortAddress(a);
export const healthOf = (digest: Digest | undefined) => (digest?.status ?? {}) as unknown as Health;
export const syncOf = (digest: Digest | undefined) => (digest?.sync ?? { last: null }) as unknown as SyncState;
export const badAccounts = (digest: Digest | undefined) => Object.entries(healthOf(digest)).filter(([, st]) => st?.health === "error").map(([a]) => a);

/** How far the Inboxes' first syncs have got, over the accounts whose older mail is still being read; undefined when none is. */
export function readingOf(digest: Digest | undefined): { done: number; total: number; inboxes: number } | undefined {
  const reading = Object.values(healthOf(digest)).filter((st) => st?.done != null && st.total != null);
  if (!reading.length) return undefined;
  return { done: reading.reduce((n, st) => n + st!.done!, 0), total: reading.reduce((n, st) => n + st!.total!, 0), inboxes: reading.length };
}

export function pillLabel(digest: Digest | undefined, manual: boolean, reconcile: boolean, now: Date): { cls: string; text: string } {
  if (!digest) return { cls: "", text: "Connecting…" };
  const bad = badAccounts(digest), sy = syncOf(digest), read = readingOf(digest);
  if (bad.length) return { cls: "err", text: bad.length === 1 ? `${nameOf(digest, bad[0]!)} can’t connect` : `${bad.length} accounts can’t connect` };
  if (read) return { cls: "busy", text: `Reading your ${read.inboxes === 1 ? "inbox" : "inboxes"}: ${read.done.toLocaleString()} of ${read.total.toLocaleString()}` };
  if (reconcile) return { cls: "busy", text: "Reconciling…" };
  if (manual || sy.running || Object.values(healthOf(digest)).some((st) => st?.health === "syncing")) return { cls: "busy", text: "Syncing…" };
  if (digest.profile === "read-only") return { cls: "", text: "Read-only" };
  if (sy.last) {
    const d = new Date(sy.last), today = d.toDateString() === now.toDateString();
    return { cls: "ok", text: `Synced ${today ? d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }) : d.toLocaleDateString([], { month: "short", day: "numeric" })}` };
  }
  return { cls: "", text: "Not synced yet" };
}

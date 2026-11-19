// What the digest says about syncing and each account, in words. No DOM.
import type { GetAnswers } from "../../lib/api/types";
import { shortAddress } from "../../lib/text";

export type Digest = GetAnswers["/api/digest"];
// The generated type sees only what the tests' worlds answered.
type Health = Record<string, { health?: string; done?: number | null; total?: number | null; trying_again?: boolean | null } | undefined>;
interface SyncState { last: string | null; result?: string; running?: boolean }

export const nameOf = (digest: Digest | undefined, a: string) => digest?.aliases?.[a] || shortAddress(a);
export const healthOf = (digest: Digest | undefined) => (digest?.status ?? {}) as unknown as Health;
export const syncOf = (digest: Digest | undefined) => (digest?.sync ?? { last: null }) as unknown as SyncState;
export const badAccounts = (digest: Digest | undefined) => Object.entries(healthOf(digest)).filter(([, st]) => st?.health === "error").map(([a]) => a);

/** How far the Inboxes' first syncs have got, over the accounts whose older mail is still being read; undefined when none is.
 * Only the accounts the digest lists count: one removed while it was read is no inbox of yours. */
export function readingOf(digest: Digest | undefined): { done: number; total: number; inboxes: number } | undefined {
  const status = healthOf(digest);
  const reading = (digest?.accounts ?? []).map((a) => status[a]).filter((st) => st?.done != null && st.total != null);
  if (!reading.length) return undefined;
  return { done: reading.reduce((n, st) => n + st!.done!, 0), total: reading.reduce((n, st) => n + st!.total!, 0), inboxes: reading.length };
}

/** The pill's quiet words while an account's last sync failed and it is trying again. */
const TRYING_AGAIN = "Couldn’t reach the server · trying again";

export function pillLabel(digest: Digest | undefined, manual: boolean, reconcile: boolean, now: Date): { cls: string; text: string } {
  if (!digest) return { cls: "", text: "Connecting…" };
  const bad = badAccounts(digest), sy = syncOf(digest), read = readingOf(digest);
  if (bad.length) return { cls: "err", text: bad.length === 1 ? `${nameOf(digest, bad[0]!)} can’t connect` : `${bad.length} accounts can’t connect` };
  if (read) return { cls: "busy", text: `Reading your ${read.inboxes === 1 ? "inbox" : "inboxes"}: ${read.done.toLocaleString()} of ${read.total.toLocaleString()}` };
  if (reconcile) return { cls: "busy", text: "Reconciling…" };
  if (manual || sy.running || Object.values(healthOf(digest)).some((st) => st?.health === "syncing")) return { cls: "busy", text: "Syncing…" };
  // An account's last sync failed, not yet for long enough to say it can't connect: the sync's round says when it ended,
  // but nothing synced then, so it never says Synced (as the Mac's StatusWords.pill).
  if ((digest.accounts ?? []).some((a) => healthOf(digest)[a]?.trying_again)) return { cls: "busy", text: TRYING_AGAIN };
  if (digest.profile === "read-only") return { cls: "", text: "Read-only" };
  if (sy.last) {
    const d = new Date(sy.last), today = d.toDateString() === now.toDateString();
    return { cls: "ok", text: `Synced ${today ? d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }) : d.toLocaleDateString([], { month: "short", day: "numeric" })}` };
  }
  return { cls: "", text: "Not synced yet" };
}

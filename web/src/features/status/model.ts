// What the digest says about syncing and each account, in words. No DOM.
import type { GetAnswers } from "../../lib/api/types.gen";
import { shortAddress } from "../../lib/text";

export type Digest = GetAnswers["/api/digest"];
// The generated type sees only what the tests' worlds answered.
type Health = Record<string, { health?: string } | undefined>;
interface SyncState { last: string | null; result?: string; running?: boolean }

export const nameOf = (digest: Digest | undefined, a: string) => digest?.aliases?.[a] || shortAddress(a);
export const healthOf = (digest: Digest | undefined) => (digest?.status ?? {}) as unknown as Health;
export const syncOf = (digest: Digest | undefined) => (digest?.sync ?? { last: null }) as unknown as SyncState;
export const badAccounts = (digest: Digest | undefined) => Object.entries(healthOf(digest)).filter(([, st]) => st?.health === "error").map(([a]) => a);

export function pillLabel(digest: Digest | undefined, manual: boolean, reconcile: boolean, now: Date): { cls: string; text: string } {
  if (!digest) return { cls: "", text: "Connecting…" };
  const bad = badAccounts(digest), sy = syncOf(digest);
  if (bad.length) return { cls: "err", text: bad.length === 1 ? `${nameOf(digest, bad[0]!)} can’t connect` : `${bad.length} accounts can’t connect` };
  if (reconcile) return { cls: "busy", text: "Reconciling…" };
  if (manual || sy.running || Object.values(healthOf(digest)).some((st) => st?.health === "syncing")) return { cls: "busy", text: "Syncing…" };
  if (digest.profile === "read-only") return { cls: "", text: "Read-only" };
  if (sy.last) {
    const d = new Date(sy.last), today = d.toDateString() === now.toDateString();
    return { cls: "ok", text: `Synced ${today ? d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }) : d.toLocaleDateString([], { month: "short", day: "numeric" })}` };
  }
  return { cls: "", text: "Not synced yet" };
}

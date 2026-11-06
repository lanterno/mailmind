// Mail landed somewhere other than the list you're on. A list you're on refreshes in place, so this says so only for the
// rest, with a line from the AI on what came in since you last looked. The line never asks at the daily limit.
import { get, post } from "../../api/client";
import { go, type ListSource, type Route } from "../../app/route";
import { createStore, store, stored } from "../../lib/store";

interface Pending { count: number; account: string; since: number; headline: string }
const pending = createStore<Pending | null>(null);

const LOOK_KEY = "mm_lastlook";
const FIRST_LOOK_MS = 6 * 3600 * 1000;

function looks(): Record<string, number> {
  try {
    return JSON.parse(stored(LOOK_KEY) ?? "{}") as Record<string, number>;
  } catch {
    return {};
  }
}

// The watermark moves only when you really look: an Inbox opened or the tab come back to, never on a background tick.
export function lookedAt(source: ListSource | null, accounts: string[]) {
  const inbox = source?.kind === "allInboxes" ? accounts : source?.kind === "folder" && source.folder.toUpperCase() === "INBOX" ? [source.account] : [];
  if (!inbox.length) return;
  const all = looks(), now = Date.now();
  for (const a of inbox) all[a] = now;
  store(LOOK_KEY, JSON.stringify(all));
  pending.set(null);
}

const landedHere = (source: ListSource | null, account: string, folder: string) =>
  source?.kind === "allInboxes" ? folder.toUpperCase() === "INBOX"
    : source?.kind === "folder" && source.account === account && source.folder.toUpperCase() === folder.toUpperCase();

let writing = false, timer: ReturnType<typeof setTimeout> | undefined;
async function headline(ai: boolean) {
  const p = pending.get();
  if (!p || !ai || writing) return;
  writing = true;
  try {
    const since = Math.floor(p.since / 1000);
    let d = await get(`/api/briefing/since?since=${since}&account=${encodeURIComponent(p.account)}`);
    if (d.missing) d = await post("/api/briefing/since", { since, account: p.account });
    const now = pending.get();
    if (d.ok && !d.empty && d.headline && now) pending.set({ ...now, headline: d.headline });
  } catch { /* the plain count stands */ } finally {
    writing = false;
  }
}

export function onNewMail(d: { account?: string; folder?: string; count?: number }, route: Route, ai: boolean) {
  const account = d.account ?? "", folder = d.folder ?? "INBOX", count = d.count || 1;
  if (landedHere(route.source, account, folder)) return;
  const was = pending.get();
  pending.set({
    count: (was?.count ?? 0) + count,
    account: was && was.account !== account ? "" : account,
    since: was?.since ?? (looks()[account] || Date.now() - FIRST_LOOK_MS),
    headline: was?.headline ?? "",
  });
  clearTimeout(timer);
  timer = setTimeout(() => void headline(ai), 1500);   // a burst is one call
}

export function NewMailPill() {
  const p = pending.useValue();
  if (!p) return null;
  const head = `▾ ${p.count} new — open the Inbox`;
  return (
    <button
      id="newPill"
      className={p.headline ? "new-pill new-pill--ai" : "new-pill"}
      onClick={() => go({ source: p.account ? { kind: "folder", account: p.account, folder: "INBOX" } : { kind: "allInboxes" }, opened: null })}
    >
      {p.headline ? <><span className="np-head">{head}</span><span className="np-delta">{p.headline}</span></> : head}
    </button>
  );
}

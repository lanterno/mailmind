// Mail landed somewhere other than the list you're on. A list you're on refreshes in place, so this says so only for the
// rest, with a line from the AI on what came in since you last looked. The line never asks at the daily limit.
import { get, post } from "../../lib/api/client";
import { go, type ListSource, type Route } from "../../app/route";
import { mayNotify } from "../../lib/prefs";
import { createStore, store, stored } from "../../lib/store";
import { announce } from "../../lib/toast";

interface Pending { count: number; woke: number; account: string; since: number; headline: string }
const pending = createStore<Pending | null>(null);

// When each account's Inbox was last looked at, by the account's place in the list the page has, so this browser's storage holds
// no address. It once kept them by address, under a key let go of as the first look is read.
const LOOK_KEY = "mm_looked";
const LOOK_KEY_BY_ADDRESS = "mm_lastlook";
const FIRST_LOOK_MS = 6 * 3600 * 1000;

function looks(): Record<string, number> {
  store(LOOK_KEY_BY_ADDRESS, null);
  try {
    const said: unknown = JSON.parse(stored(LOOK_KEY) ?? "{}");
    return typeof said === "object" && said !== null && !Array.isArray(said) ? said as Record<string, number> : {};
  } catch {
    return {};
  }
}

const lookOf = (accounts: string[], account: string) => {
  const at = accounts.indexOf(account);
  return at < 0 ? 0 : Number(looks()[at]) || 0;
};

// The watermark moves only when you really look: an Inbox opened or the tab come back to, never on a background tick.
export function lookedAt(source: ListSource | null, accounts: string[]) {
  const inbox = source?.kind === "allInboxes" ? accounts : source?.kind === "folder" && source.folder.toUpperCase() === "INBOX" ? [source.account] : [];
  if (!inbox.length) return;
  const all = looks(), now = Date.now();
  for (const at of inbox.map((a) => accounts.indexOf(a))) if (at >= 0) all[at] = now;
  store(LOOK_KEY, JSON.stringify(all));
  pending.set(null);
}

const landedHere = (source: ListSource | null, account: string, folder: string) =>
  source?.kind === "allInboxes" ? folder.toUpperCase() === "INBOX"
    : source?.kind === "folder" && source.account === account && source.folder.toUpperCase() === folder.toUpperCase();

let writing = false, aiOn = false, timer: ReturnType<typeof setTimeout> | undefined;
async function headline(ai: boolean) {
  const p = pending.get();
  if (!p?.count || !ai || writing || document.hidden) return;     // nobody's reading it, and asking would count as someone here
  writing = true;
  try {
    const since = Math.floor(p.since / 1000);
    let d = await get(`/api/briefing/since?since=${since}&account=${encodeURIComponent(p.account)}`);
    if ("missing" in d) d = await post("/api/briefing/since", { since, account: p.account });
    const now = pending.get();
    if (!d.empty && "headline" in d && d.headline && now) pending.set({ ...now, headline: d.headline });
  } catch { /* the plain count stands */ } finally {
    writing = false;
  }
}

// In the background mail counts in the tab's title and on the desktop. With someone here, a screen reader hears of it.
let cameIn = 0;
const showTitle = () => { document.title = cameIn > 0 ? `(${cameIn}) MailMind` : "MailMind"; };
export function cameBack() {
  cameIn = 0;
  showTitle();
  const p = pending.get();
  if (p && !p.headline) {
    clearTimeout(timer);
    timer = setTimeout(() => void headline(aiOn), 1500);
  }
}

function tell(account: string, count: number, name: string) {
  if (!document.hidden) {
    announce(`${count} new message${count === 1 ? "" : "s"} arrived${account ? ` in ${name}` : ""}`);
    return;
  }
  cameIn += count;
  showTitle();
  if (typeof Notification === "undefined" || Notification.permission !== "granted" || !mayNotify("notify_new_mail")) return;
  try {
    const n = new Notification(`New mail · ${name}`, { body: `${count} new message${count > 1 ? "s" : ""}`, tag: `mailmind-${account}`, silent: false });
    n.onclick = () => {
      window.focus();
      n.close();
    };
  } catch { /* the title still counts it */ }
}

export function onNewMail(d: { account?: string; folder?: string; count?: number }, route: Route, ai: boolean, nameOf: (account: string) => string, listed: string[]) {
  const account = d.account ?? "", folder = d.folder ?? "INBOX", count = d.count || 1;
  tell(account, count, nameOf(account));
  if (landedHere(route.source, account, folder)) return;
  const was = pending.get();
  pending.set({
    count: (was?.count ?? 0) + count,
    woke: was?.woke ?? 0,
    account: was && was.account !== account ? "" : account,
    since: was?.since ?? (lookOf(listed, account) || Date.now() - FIRST_LOOK_MS),
    headline: was?.headline ?? "",
  });
  clearTimeout(timer);
  aiOn = ai;
  timer = setTimeout(() => void headline(ai), 1500);   // a burst is one call
}

// Mail back from a snooze, and no Inbox on screen to show it: nobody saw it happen, so it stays said (see lib/toast.tsx) until
// the Inbox is looked at. An Inbox on screen shows it as it does new mail, and nothing is said over that.
export function onSnoozeWake(d: { accounts?: string[]; count?: number }, route: Route, listed: string[]) {
  const accounts = d.accounts ?? [], account = accounts.length === 1 ? accounts[0]! : "", woke = d.count || 1;
  if (landedHere(route.source, account, "INBOX")) return;
  if (!document.hidden) announce(`${woke} snoozed message${woke === 1 ? "" : "s"} back`);
  const was = pending.get();
  pending.set({
    count: was?.count ?? 0,
    woke: (was?.woke ?? 0) + woke,
    account: was && was.account !== account ? "" : account,
    since: was?.since ?? (lookOf(listed, account) || Date.now() - FIRST_LOOK_MS),
    headline: was?.headline ?? "",
  });
}

export function NewMailPill() {
  const p = pending.useValue();
  if (!p) return null;
  const head = `▾ ${[p.count ? `${p.count} new` : "", p.woke ? `${p.woke} back from snooze` : ""].filter(Boolean).join(", ")} — open the Inbox`;
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

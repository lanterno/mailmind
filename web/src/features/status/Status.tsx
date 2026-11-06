// The pill names the one thing that matters about syncing; its popover has the rest. Beside it: an account that can't
// connect says so across the top, and a meeting about to start gets a reminder.
import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query";
import { Dialog, Popover } from "react-aria-components";
import { get, post } from "../../lib/api/client";
import { minute } from "../../lib/clock";
import { createStore } from "../../lib/store";
import { usd } from "../../lib/text";
import { announce, toast } from "../../lib/toast";
import { Icon } from "../../lib/ui/Icon";
import { useFocusFirst } from "../../lib/ui/menuKeys";
import { AI_PRIVACY } from "../assistant/Brief";
import { eventWhen } from "../assistant/model";
import { readAgain } from "../mailbox/actions";
import { useDigest } from "../mailbox/data";
import type { Mailbox } from "../mailbox/useMailbox";
import { badAccounts, healthOf, nameOf, pillLabel, syncOf, type Digest } from "./model";
import { curateOpen } from "../curation/open";

const syncing = createStore(false);
const reconciling = createStore(false);

const pause = (ms: number) => new Promise((done) => setTimeout(done, ms));

export async function syncNow(qc: QueryClient) {
  if (syncing.get()) return;
  syncing.set(true);
  toast("Syncing…");
  try {
    await post("/api/refresh", {});
  } catch { /* the next digest says how it is */ }
  for (let n = 0; n < 40; n++) {
    await pause(3000);
    try {
      const d = await get("/api/digest?min=1&account=");
      qc.setQueryData(["digest"], d);
      if (!syncOf(d).running) break;
    } catch { /* try again */ }
  }
  syncing.set(false);
  readAgain(qc);
  toast("Sync complete");
}

export async function reconcile(qc: QueryClient) {
  if (reconciling.get()) return;
  reconciling.set(true);
  try {
    const dry = await post("/api/reconcile", { account: "all", dry_run: true });
    if (!dry.total_ghosts) toast("Nothing to reconcile — the record is straight.");
    else {
      toast(`Reconciling — striking ${dry.total_ghosts} ghosts…`);
      const real = await post("/api/reconcile", { account: "all", dry_run: false });
      readAgain(qc);
      toast(`Struck ${(real.total_pruned ?? 0).toLocaleString()} deleted items`);
    }
  } catch {
    toast("Reconcile failed");
  }
  reconciling.set(false);
}

function AiLine({ digest }: { digest: Digest }) {
  const a = digest.ai;
  if (!a) return null;
  if (a.engine === "none") return <div className="sm-sub sm-ai"><Icon name="wind" /><span>AI is off · no API key</span></div>;
  const failing = a.last_ok === false && a.last_error;
  const spent = a.spent_today_micros, limit = a.limit_micros, batch = (a as { batch_waiting?: number }).batch_waiting;
  const what = `Claude API · ${a.model}${spent > 0 ? ` · ${usd(spent)}${limit ? ` of ${usd(limit)}` : ""} today` : ""}${batch ? ` · ${batch} out in a batch` : ""}`;
  return (
    <>
      <div className={failing ? "sm-sub sm-ai err" : "sm-sub sm-ai"}><Icon name="wind" /><span>{what}{failing ? ` · last call failed: ${a.last_error}` : ""}</span></div>
      <div className="sm-sub sm-ai m"><Icon name="lock" /><span>{AI_PRIVACY}</span></div>
    </>
  );
}

function StatusMenu({ digest, label, close, m }: { digest: Digest; label: { cls: string; text: string }; close: () => void; m: Mailbox }) {
  const qc = useQueryClient();
  const inside = useFocusFirst<HTMLDivElement>("button:not(:disabled)");
  const reconcilingNow = reconciling.useValue();
  const stats = useQuery({ queryKey: ["stats"], queryFn: () => get("/api/stats") });     // an IMAP scan: only when asked
  const accounts = digest.accounts ?? [], health = healthOf(digest), sy = syncOf(digest), bad = badAccounts(digest);
  const s = digest.stats;
  const inboxes = stats.data?.totals?.in_inbox;
  const ask = typeof Notification !== "undefined" && Notification.permission === "default";
  const act = (run: () => void) => () => {
    close();
    run();
  };
  return (
    <div ref={inside}>
      <div className="sm-info">
        <div className="sm-hd"><i className={`sdot ${label.cls}`} aria-hidden="true" />{label.text}</div>
        {digest.profile === "read-only" && <div className="sm-sub">MailMind is running read-only: it writes nothing to your mail servers and syncs only when asked. What you file, star or send waits for a full run.</div>}
        {sy.last && sy.result && <div className="sm-sub">Last sync: {sy.result}</div>}
        <div className="sm-sub">{inboxes !== undefined ? <><b>{inboxes.toLocaleString()}</b> in your inboxes</> : "counting your inboxes…"} · <b>{(s?.summarized ?? 0).toLocaleString()}</b> summarized</div>
        <AiLine digest={digest} />
        {(accounts.length > 1 || bad.length > 0) && (
          <div className="sm-accts">
            {accounts.map((a) => {
              const h = health[a]?.health ?? "";
              return (
                <div className="sm-acct" key={a}>
                  <i className={`sdot ${h === "error" ? "err" : h === "syncing" ? "busy" : h === "ok" ? "ok" : ""}`} aria-hidden="true" />
                  <span>{nameOf(digest, a)}</span>
                  <small className={h === "error" ? "err" : undefined}>{h === "error" ? "can’t connect" : h === "syncing" ? "syncing…" : h === "ok" ? "connected" : ""}</small>
                </div>
              );
            })}
          </div>
        )}
      </div>
      <div className="sm-acts">
        <button disabled={label.text === "Syncing…"} onClick={act(() => void syncNow(qc))}><span className="mi-ic" aria-hidden="true"><Icon name="refresh" /></span>Sync now</button>
        <button disabled={reconcilingNow} onClick={act(() => void reconcile(qc))}><span className="mi-ic" aria-hidden="true"><Icon name="restore" /></span>Reconcile<small>drop mail deleted elsewhere</small></button>
        <button onClick={act(() => curateOpen.set(true))}><span className="mi-ic" aria-hidden="true"><Icon name="leaf" /></span>Curate<small>noisy senders, rules, audit</small></button>
        {bad.length > 0 && <button onClick={act(m.openSettings)}><span className="mi-ic" aria-hidden="true"><Icon name="sliders" /></span>Fix connections in Settings</button>}
        {ask && <button onClick={act(() => void Notification.requestPermission().then((p) => toast(p === "granted" ? "Notifications on" : "Notifications blocked")))}><span className="mi-ic" aria-hidden="true"><Icon name="bell" /></span>Turn on desktop notifications</button>}
      </div>
    </div>
  );
}

export function StatusPill({ m }: { m: Mailbox }) {
  const { data: digest } = useDigest();
  const manual = syncing.useValue();
  const reconcilingNow = reconciling.useValue();
  const now = new Date(minute.useValue());
  const [open, setOpen] = useState(false);
  const button = useRef<HTMLButtonElement>(null);
  const label = pillLabel(digest, manual, reconcilingNow, now);
  return (
    <>
      <button ref={button} className="status-pill" id="statusBtn" aria-haspopup="dialog" aria-expanded={open} aria-label={`Sync status: ${label.text}`} title="Sync, reconcile and curation" onClick={() => setOpen(!open)}>
        <i className={`sdot ${label.cls}`} aria-hidden="true" /><span id="statusTxt">{label.text}</span>
      </button>
      {digest && (
        <Popover className="status-menu rac" isOpen={open} triggerRef={button} placement="bottom end" offset={6} onOpenChange={setOpen}>
          <Dialog aria-label="Sync status" className="rac-col">
            <StatusMenu digest={digest} label={label} close={() => setOpen(false)} m={m} />
          </Dialog>
        </Popover>
      )}
    </>
  );
}

// Across the top, until you dismiss it, for as long as an account can't connect.
const dismissed = createStore<ReadonlySet<string>>(new Set());
export function AccountAlert({ m }: { m: Mailbox }) {
  const { data: digest } = useDigest();
  const gone = dismissed.useValue();
  const bad = badAccounts(digest).filter((a) => !gone.has(a));
  if (!bad.length) return null;
  return (
    <div className="acct-alert" id="acctAlert">
      <span><Icon name="alert" /> {bad.length} account{bad.length > 1 ? "s" : ""} couldn’t connect ({bad.map((a) => nameOf(digest, a)).join(", ")}). </span>
      <button className="aa-fix" id="aaFix" onClick={m.openSettings}>Open Settings</button>
      <button className="aa-x" id="aaX" aria-label="Dismiss" onClick={() => dismissed.set(new Set([...gone, ...bad]))}>✕</button>
    </div>
  );
}

const SOON_MS = 10 * 60000, SOON_AFTER = 5 * 60000;
const soonGone = createStore<ReadonlySet<string>>(new Set());
const told = new Set<string>();

export function SoonPill({ m }: { m: Mailbox }) {
  const { data } = useQuery({ queryKey: ["upcoming"], queryFn: () => get("/api/upcoming"), staleTime: 60_000, refetchInterval: 60_000 });
  const now = minute.useValue();
  const gone = soonGone.useValue();
  const event = (data?.items ?? []).find((x) => {
    const d = new Date(x.start).getTime() - now;
    return !x.all_day && !gone.has(`${x.pk}|${x.start}`) && d <= SOON_MS && d > -SOON_AFTER;
  });
  const key = event ? `${event.pk}|${event.start}` : "";
  const minutes = event ? Math.ceil((new Date(event.start).getTime() - now) / 60000) : 0;
  const when = minutes > 0 ? `in ${minutes} min` : "now";
  const title = event?.summary || "(no title)";
  const message = `${title} starts ${when}`;
  useEffect(() => {
    if (!key || told.has(key)) return;
    told.add(key);
    if (document.hidden && typeof Notification !== "undefined" && Notification.permission === "granted") {
      try {
        const n = new Notification(message, { tag: `mailmind-soon-${key}` });
        n.onclick = () => {
          window.focus();
          n.close();
        };
      } catch { /* the pill still shows */ }
    } else announce(message);
  }, [key]);
  if (!event) return null;
  return (
    <span className="soon-pill" id="soonPill">
      <button
        className="sp-open"
        id="soonOpen"
        title={[eventWhen(event, new Date(now))[0], event.location, event.organizer].filter(Boolean).join(" · ")}
        aria-label={`${message}${event.location ? `, ${event.location}` : ""}. Open the invite`}
        onClick={() => m.openAny(event.pk)}
      >
        <Icon name="cal" /><b id="soonTitle">{title}</b><span id="soonIn">{when}</span>
      </button>
      <button
        className="sp-x"
        id="soonX"
        aria-label="Dismiss the reminder"
        onClick={() => {
          soonGone.set(new Set([...gone, key]));
          document.getElementById("statusBtn")?.focus();
        }}
      >×</button>
    </span>
  );
}

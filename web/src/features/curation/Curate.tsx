// The drawer for looking after the mailbox itself: senders flooding you, what each inbox holds, the rules MailMind has
// learned, your filters, and what it has done. Sweeping and cleaning up both show what they would do before they do it.
import { useState } from "react";
import { useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query";
import { Dialog, Modal, ModalOverlay } from "react-aria-components";
import { get, post, refusalOf } from "../../lib/api/client";
import type { BlockScope, GetAnswers, RuleChange } from "../../lib/api/types";
import { entries } from "../../lib/entries";
import { go } from "../../app/route";
import { useLayer } from "../../lib/backTrap";
import { createStore } from "../../lib/store";
import { shortAddress } from "../../lib/text";
import { toast } from "../../lib/toast";
import { Icon } from "../../lib/ui/Icon";
import { offerUndo, readAgain, undoGroup, useDigest } from "../mailbox";
import { FilterDialog, Filters } from "./Filters";
import { curateOpen } from "./open";

const BUCKET_COLOR: Record<string, string> = { "spam/scam": "--scam", blocked: "--block", "newsletters/marketing": "--mkt", "automated-notifications": "--notif", "receipts/transactional": "--txn", "personal/protected": "--keep", "other/personal-ish": "--personal" };

type Sender = GetAnswers["/api/cleanup/sender"];
interface Cleaning { d: Sender; scope: BlockScope; unsubscribe: boolean; armed: boolean }
// How far a block may go, as the server has it, in the toggle's order and words.
const SCOPES = entries<BlockScope, string>({ sender: "this sender", domain: "whole domain" });
const cleaning = createStore<Cleaning | null>(null);
const CLEANED: Record<string, string> = { unsubscribed_oneclick: "unsubscribed", mailto_sent: "unsub email sent", needs_browser: "opened the unsub page", no_method: "no unsub link", failed: "unsub couldn’t send — retry later", no_token: "unsub not sent — reopen to retry", read_only: "unsub not sent — MailMind is read-only", skipped: "" };

const again = (qc: QueryClient) => {
  readAgain(qc);
  for (const key of ["flood", "stats", "rules", "audit"]) void qc.invalidateQueries({ queryKey: [key] });
};

async function previewCleanup(addr: string) {
  try {
    const d = await get(`/api/cleanup/sender?addr=${encodeURIComponent(addr)}`);
    const method = d.unsub?.method ?? "none";
    cleaning.set({ d, scope: "sender", unsubscribe: method === "one-click" || method === "mailto", armed: false });
  } catch (e) {
    const no = refusalOf(e);
    toast(no ? no.detail || "error" : "Couldn’t load sender");
  }
}

async function runCleanup(qc: QueryClient, p: Cleaning) {
  const method = p.d.unsub?.method ?? "none";
  // The unsubscribe page opens in this click's gesture, or a popup blocker takes it. No "noopener" here: that would leave
  // nothing to navigate, so the opener is cut by hand below.
  const tab = method === "http-page" && p.unsubscribe ? window.open("about:blank", "_blank") : null;
  cleaning.set(null);
  toast(`Cleaning up ${p.d.from_addr}…`);
  let d;
  try {
    d = await post("/api/cleanup/execute", { from_addr: p.d.from_addr, block_scope: p.scope, do_unsubscribe: p.unsubscribe, confirm_token: p.d.confirm_token, undo_group: undoGroup() });
  } catch {
    tab?.close();
    return toast("Cleanup failed");
  }
  const url = (d as { browser_url?: string }).browser_url;
  if (tab) {
    if (url) {
      tab.opener = null;
      tab.location = url;
    } else tab.close();
  }
  const extra = CLEANED[d.status ?? ""] ?? "";
  toast(`Blocked · swept ${d.swept}${extra ? ` · ${extra}` : ""}`);
  offerUndo(`Cleaned up ${p.d.from_addr}`);
  again(qc);
}

export function CleanupPanel() {
  const qc = useQueryClient();
  const p = cleaning.useValue();
  useLayer(p !== null, () => cleaning.set(null));
  const d = p?.d, method = d?.unsub?.method ?? "none";
  const unsubWords = method === "one-click" ? <>Send a <b>one-click</b> unsubscribe to {d?.unsub?.https_host || "the sender"} — no email, no browser</>
    : method === "mailto" ? <>Email the list’s unsubscribe address <b>{d?.unsub?.mailto_to}</b> from your account</>
      : <>Open the unsubscribe <b>page</b> in a new tab (nothing is auto-submitted)</>;
  const accounts = d?.accounts ?? [];
  return (
    <ModalOverlay className="do-overlay show" isOpen={p !== null} isDismissable onOpenChange={(open) => { if (!open) cleaning.set(null); }}>
      <Modal className="modal-through">
        <Dialog aria-label="Clean up a sender" className="do-panel">
          {p && d && (
            <div className="do-plan do-plan-danger">
              <div className="do-hd">Clean up <b>{d.from_addr}</b>?</div>
              <div className="do-scope">{d.count} message{d.count === 1 ? "" : "s"} in your synced mail{accounts.length > 1 ? ` · ${accounts.length} inboxes` : ""}</div>
              <div className="do-line rev"><span className="ic">✓</span><span>Block <b>{p.scope === "domain" ? d.domain : d.from_addr}</b> — future mail auto-filed</span><span className="tag">reversible</span></div>
              <div className="do-scopetoggle">
                {SCOPES.map(([s, label]) => <button key={s} className={p.scope === s ? "on" : ""} aria-pressed={p.scope === s} onClick={() => cleaning.set({ ...p, scope: s, armed: false })}>{label}</button>)}
              </div>
              <div className="do-line rev"><span className="ic">✓</span><span>Move <b>{d.sweep_count}</b> inbox message{d.sweep_count === 1 ? "" : "s"}{p.scope === "domain" ? ` from ${d.from_addr}` : ""} to Trash</span><span className="tag">reversible</span></div>
              {method === "none"
                ? <div className="do-line"><span className="ic">·</span><span style={{ color: "var(--faint)" }}>No List-Unsubscribe found — block + sweep only</span></div>
                : <div className="do-line irrev"><span className="ic"><Icon name="alert" /></span><span>{unsubWords}</span><span className="tag">can’t undo</span></div>}
              {method !== "none" && (
                <label className="do-unsubopt">
                  <input type="checkbox" checked={p.unsubscribe} onChange={(e) => cleaning.set({ ...p, unsubscribe: e.target.checked })} /> {method === "http-page" ? "open the unsubscribe page" : "also send the unsubscribe now"}
                </label>
              )}
              {d.sweep_capped && <div className="do-cap"><Icon name="alert" /> Sweeps the most recent {d.sweep_count} — run again for the rest.</div>}
              <div className="do-danger-note">Block + sweep undo with <b>z</b>. {method === "none" ? "" : "Unsubscribing can’t be undone."}</div>
              <div className="do-foot">
                <button className={p.armed ? "btn-retry strong danger armed" : "btn-retry strong danger"} onClick={() => { if (p.armed) void runCleanup(qc, p); else cleaning.set({ ...p, armed: true }); }}>{p.armed ? "Click again to confirm" : "Clean up"}</button>
                <button className="do-cancel" onClick={() => cleaning.set(null)}>Cancel</button>
              </div>
            </div>
          )}
        </Dialog>
      </Modal>
    </ModalOverlay>
  );
}

function Flood() {
  const { data, isError } = useQuery({ queryKey: ["flood"], queryFn: () => get("/api/cleanup/senders") });
  if (isError) return <div style={{ color: "var(--faint)", fontSize: 11 }}>Couldn’t load.</div>;
  if (!data) return <div style={{ color: "var(--faint)", fontSize: 11 }}>Measuring…</div>;
  const senders = data.senders ?? [];
  if (!senders.length) return <div style={{ color: "var(--faint)", fontSize: 11.5, padding: "6px 2px" }}>No sender is flooding you right now.</div>;
  return (
    <>
      {senders.map((x) => (
        // eslint-disable-next-line jsx-a11y/click-events-have-key-events, jsx-a11y/no-static-element-interactions -- the pointer's larger target for the row's own button, which the keyboard has
        <div className="flood-row" key={x.from_addr} data-flood={x.from_addr} onClick={() => void previewCleanup(x.from_addr)}>
          <div className="flood-main">
            <div className="flood-nm">{x.from_name || x.from_addr}</div>
            <div className="flood-ad">{x.from_addr}</div>
            <div className="flood-chips">
              <span className="flood-chip">{x.count} msg{x.count === 1 ? "" : "s"}</span>
              {x.unseen > 0 && <span className={(x.unseen_ratio || 0) >= 0.7 ? "flood-chip hot" : "flood-chip"}>{x.unseen} unread</span>}
              {(x.accounts?.length ?? 0) > 1 && <span className="flood-chip multi">{x.accounts.length} inboxes</span>}
              {x.lu_likely && <span className="flood-chip lu1">1-click</span>}
              {x.last_seen && <span className="flood-chip">{x.last_seen}</span>}
            </div>
          </div>
          <div className="flood-side">
            <span className="flood-score" title="noise score">{x.noise_score}</span>
            <button className="flood-go" aria-label={`Clean up ${x.from_addr}`}>Clean up</button>
          </div>
        </div>
      ))}
    </>
  );
}

function Inboxes() {
  const qc = useQueryClient();
  const abort = () => {
    setArmed(null);
    toast("Aborted");
  };
  const { data: digest } = useDigest();
  const { data } = useQuery({ queryKey: ["stats"], queryFn: () => get("/api/stats") });
  const [armed, setArmed] = useState<{ account: string; planned: number } | null>(null);
  const nameOf = (a: string) => digest?.aliases?.[a] || shortAddress(a);
  if (!data) return <div style={{ color: "var(--faint)", fontSize: 11 }}>Measuring live inboxes…</div>;
  const arm = async (account: string) => {
    try {
      const r = (await post("/api/sweep", { account, dry_run: true })).results[0]!;
      if (r.error) toast(`Sweep: ${r.error}`);
      else if (!r.planned) toast("Nothing to sweep");
      else setArmed({ account, planned: r.planned });
    } catch {
      toast("Plan failed");
    }
  };
  const sweep = async ({ account, planned }: { account: string; planned: number }) => {
    setArmed(null);
    toast(`Sweeping ${planned}…`);
    try {
      const r = (await post("/api/sweep", { account, dry_run: false })).results[0]!;
      toast(`Swept ${r.moved}/${r.planned}${r.stragglers?.length ? ` · ${r.stragglers.length} unverified` : ""}`);
    } catch {
      toast("Sweep failed");
    }
    again(qc);
  };
  return (
    <>
      {data.accounts.map((a) => {
        const total = a.in_inbox || 0, buckets = (a.by_bucket ?? {}) as Record<string, number>;
        const error = (a as unknown as { error?: string | null }).error;
        return (
          <div className={error ? "acctmod error" : "acctmod ok"} key={a.account} data-acct={a.account}>
            <div className="top2"><span className="led" /><span>{nameOf(a.account)}</span><span className="cnt">{error ? "—" : total}</span></div>
            <div className="microbar">
              {data.buckets.map((b) => (buckets[b.name] ? <i key={b.name} style={{ width: `${total ? (buckets[b.name]! / total) * 100 : 0}%`, background: `var(${BUCKET_COLOR[b.name] ?? "--mute"})` }} title={`${b.name}:${buckets[b.name]}`} /> : null))}
            </div>
            <div className="r3">
              <span>{error ? <span style={{ color: "var(--err)" }}>{error.slice(0, 24)}</span> : `${a.junk} junk · ${a.keep} keep`}</span>
              {a.junk > 0 && <button className="sweep" onClick={() => void arm(a.account)}>Sweep {a.junk}</button>}
            </div>
            {armed?.account === a.account && (
              // eslint-disable-next-line jsx-a11y/no-static-element-interactions -- Esc while the focus is on its buttons: the line itself takes no focus
              <div className="armline" onKeyDown={(e) => { if (e.key === "Escape") { e.stopPropagation(); abort(); } }}>
                ▸ sweep <b>{nameOf(a.account)}</b> · <b>{armed.planned}</b> msgs<br />
                {/* eslint-disable-next-line jsx-a11y/no-autofocus -- it appears because of the click just made on Sweep, and answers that click */}
                <button className="sweep" autoFocus onClick={() => void sweep(armed)}>Confirm</button> <button className="sweep" onClick={abort}>Abort</button>
              </div>
            )}
          </div>
        );
      })}
    </>
  );
}

function Rules() {
  const qc = useQueryClient();
  const { data } = useQuery({ queryKey: ["rules"], queryFn: () => get("/api/rules") });
  const [text, setText] = useState("");
  const send = async (token: string, verdict: RuleChange, say: string) => {
    try {
      const d = await post("/api/rules", { token, verdict });
      toast(say);
      void qc.invalidateQueries({ queryKey: ["rules"] });
      void qc.invalidateQueries({ queryKey: ["stats"] });
      if ((d as { reclassified?: unknown }).reclassified) readAgain(qc);     // a rule changed how stored mail reads
    } catch {
      toast("Rule failed");
    }
  };
  const r = data?.rules;
  const chips = (tokens: unknown[], cls: string) => (tokens.length
    ? tokens.map((t) => <button className={`rchip ${cls}`} key={String(t)} data-token={String(t)} title="Remove this rule" aria-label={`Remove the rule for ${String(t)}`} onClick={() => void send(String(t), "remove", `Removed ${String(t)}`)}>{String(t)}</button>)
    : <span style={{ color: "var(--faint)", fontSize: 10 }}>none</span>);
  return (
    <>
      <div className="lbl">Always keep</div><div className="lst" id="keepList">{chips([...(r?.keep_domains ?? []), ...(r?.keep_senders ?? [])], "keep")}</div>
      <div className="lbl">Always trash</div><div className="lst" id="blockList">{chips([...(r?.block_domains ?? []), ...(r?.block_senders ?? [])], "block")}</div>
      <input
        className="rule"
        id="ruleInput"
        aria-label="Add a rule"
        placeholder="domain.com / name@host — Enter keep · Shift+Enter block"
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          const t = text.trim();
          if (e.key !== "Enter" || !t) return;
          setText("");
          void send(t, e.shiftKey ? "block" : "keep", `${e.shiftKey ? "Blocked" : "Keeping"} ${t}`);
        }}
      />
    </>
  );
}

function Audit() {
  const { data: digest } = useDigest();
  const { data } = useQuery({ queryKey: ["audit"], queryFn: () => get("/api/audit?limit=60") });
  const nameOf = (a: string) => digest?.aliases?.[a] || shortAddress(a);
  const events = (data?.events ?? []) as { ts?: string; kind: string; account?: string; pruned?: number; from?: string }[];
  const openAccount = (a: string | undefined) => {
    if (!a || !digest?.accounts?.includes(a)) return;   // a removed account has no folders to open
    curateOpen.set(false);
    go({ source: { kind: "folder", account: a, folder: "INBOX" }, opened: null });
  };
  if (!events.length) return <div className="console" id="console"><div style={{ color: "var(--faint)" }}>No activity yet.</div></div>;
  return (
    <div className="console" id="console">
      {events.map((e, i) => (
        // eslint-disable-next-line jsx-a11y/click-events-have-key-events, jsx-a11y/no-static-element-interactions -- a shortcut for the pointer: the rail and the palette reach every account from the keyboard
        <div className="ln" key={i} data-acct={e.account ?? ""} onClick={() => openAccount(e.account)}>
          <span className={`k ${e.kind}`}>{e.kind}</span>
          <span className="t">{e.ts ? new Date(e.ts).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : ""}</span>
          <span>{e.kind === "reconcile" ? `struck ${e.pruned} · ${nameOf(e.account ?? "")}` : `${nameOf(e.account ?? "")} · ${(e.from ?? "").slice(0, 22)}`}</span>
        </div>
      ))}
    </div>
  );
}

export function Curate() {
  const open = curateOpen.useValue();
  useLayer(open, () => curateOpen.set(false));
  return (
    <>
      <ModalOverlay className="scrim on" isOpen={open} isDismissable onOpenChange={curateOpen.set}>
        <Modal className="drawer open">
          <Dialog aria-label="Curate" className="curate-body">
            <button className="x" id="drawerX" aria-label="Close" onClick={() => curateOpen.set(false)}>✕</button>
            <h3>Senders flooding you</h3>
            <div id="floodList"><Flood /></div>
            <h3>Inboxes</h3>
            <div id="railAccounts"><Inboxes /></div>
            <h3>Learned rules</h3>
            <Rules />
            <h3>Filters</h3>
            <Filters />
            <h3>Audit</h3>
            <Audit />
          </Dialog>
        </Modal>
      </ModalOverlay>
      <CleanupPanel />
      <FilterDialog />
    </>
  );
}

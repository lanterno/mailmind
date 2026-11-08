// Two saved searches that read better as something other than rows: newsletters as a river, receipts as a ledger.
import { useEffect } from "react";
import type { Copy } from "../../lib/copy";
import { shortAddress } from "../../lib/text";
import type { Row, Mailbox } from "../mailbox";
import { fmtMoney, ledger, monthName } from "./model";

const accountOf = (m: Mailbox, r: Row) => m.aliases[r.account] || shortAddress(r.account);

// j and k move here too, so what opens stays in view. `open` is the open copy (`copyId`).
function useOpenInView(open: Copy | null) {
  const key = open?.key ?? null, folder = open?.folder ?? "";
  useEffect(() => {
    if (key !== null) document.querySelector(`#rows [data-key="${key}"][data-folder="${CSS.escape(folder)}"]`)?.scrollIntoView({ block: "nearest" });
  }, [key, folder]);
}

export function FeedRiver({ m, footer }: { m: Mailbox; footer: React.ReactNode }) {
  useOpenInView(m.openCopy);
  if (!m.rows.length && m.list.isSuccess) return <div id="rows"><div className="nores">Your Feed is clear — no newsletters waiting.</div></div>;
  return (
    <div id="rows">
      <div className="feed-river">
        {m.rows.map((r) => (
          // eslint-disable-next-line jsx-a11y/click-events-have-key-events, jsx-a11y/no-noninteractive-element-interactions -- the pointer's shortcut: the button in its heading is the keyboard's way in
          <article key={r.id} className={`fr-card${r.seen ? "" : " unread"}${r.id === m.openId ? " reading" : ""}`} data-key={r.key} data-folder={r.folder} onClick={() => m.open(r)}>
            <div className="fr-kicker">
              <span className="fr-pub">{r.fromName || r.fromAddr}</span><span className="fr-dot">·</span><span className="fr-when">{r.when}</span>
              <span className="fr-dot">·</span><span className="fr-acct">{accountOf(m, r)}</span>
            </div>
            <h2 className="fr-head"><button className="fr-open" onClick={(e) => { e.stopPropagation(); m.open(r); }}>{r.subject || "(no subject)"}</button></h2>
            {(r.summary || r.snippet) && <p className="fr-deck">{r.summary || r.snippet}</p>}
            <button className="fr-arch" title="Read &amp; archive" aria-label={`Archive “${r.subject || "(no subject)"}”`} onClick={(e) => { e.stopPropagation(); m.file(r, "archive"); }}>Archive ↓</button>
          </article>
        ))}
        {footer}
      </div>
    </div>
  );
}

export function PaperTrail({ m, footer }: { m: Mailbox; footer: React.ReactNode }) {
  useOpenInView(m.openCopy);
  if (!m.rows.length && m.list.isSuccess) return <div id="rows"><div className="nores">No receipts or invoices yet.</div></div>;
  return (
    <div id="rows">
      <div className="paper-trail">
        {ledger(m.rows).map((month) => (
          <section key={month.ym || "undated"} aria-label={monthName(month.ym)}>
            <div className="pt-month">
              <span className="pt-mname">{monthName(month.ym)}</span>
              <span className="pt-mtot">
                {month.lines.length} item{month.lines.length === 1 ? "" : "s"}
                {month.totals.length > 0 && "  —  "}
                {month.totals.map(([cur, val], i) => <span key={cur}>{i > 0 && "  ·  "}<b>{fmtMoney(cur, val)}</b></span>)}
              </span>
            </div>
            {month.lines.map(({ row, amount, refund, shown }) => (
              <button key={row.id} className={`pt-row${row.seen ? "" : " unread"}${row.id === m.openId ? " reading" : ""}`} data-key={row.key} data-folder={row.folder} onClick={() => m.open(row)}>
                <div className="pt-main">
                  <div className="pt-merch">{row.fromName || row.fromAddr} <span className="fr-acct" style={{ fontSize: 10 }}>· {accountOf(m, row)}</span></div>
                  <div className="pt-desc">{refund ? "↩ " : ""}{row.subject || "(no subject)"}</div>
                </div>
                <div>
                  <div className={`pt-amount${amount ? (refund ? " refund" : "") : " none"}`}>{amount ? fmtMoney(amount.cur, shown!) : "—"}</div>
                  <div className="pt-date">{row.when}</div>
                </div>
              </button>
            ))}
          </section>
        ))}
        {footer}
      </div>
    </div>
  );
}

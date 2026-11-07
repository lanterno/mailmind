// Two looks over the same mail, each its own overlay: the AI's to-dos as one checkable agenda, and the week behind you.
import { Dialog, Modal, ModalOverlay } from "react-aria-components";
import { ApiError } from "../../lib/api/client";
import { createStore } from "../../lib/store";
import { Icon } from "../../lib/ui/Icon";
import { filing } from "../mailbox";
import type { Mailbox } from "../mailbox";
import { useReview, type Review } from "./data";
import { COMMIT_CAP, commitments } from "./model";
import { useLayer } from "../../lib/backTrap";

export const panel = createStore<"commits" | "week" | null>(null);
const close = () => panel.set(null);

function Overlay({ open, label, wide, children }: { open: boolean; label: string; wide?: boolean; children: React.ReactNode }) {
  return (
    <ModalOverlay className="commit-overlay show" isOpen={open} isDismissable onOpenChange={(o) => { if (!o) close(); }}>
      <Modal className="modal-through">
        <Dialog aria-label={label} className={wide ? "commit-panel rv-panel" : "commit-panel"}>{children}</Dialog>
      </Modal>
    </ModalOverlay>
  );
}

function Commitments({ m }: { m: Mailbox }) {
  const hidden = filing.useValue();
  const all = commitments((m.digest?.items ?? []).filter((it) => !hidden.has(it.pk)));
  const shown = all.slice(0, COMMIT_CAP);
  return (
    <>
      <div className="commit-hd"><Icon name="todo" /> Commitments <span className="n" id="commitN">{all.length > COMMIT_CAP ? `${all.length} · top ${COMMIT_CAP}` : all.length}</span><span className="sub">the AI's to-dos pulled from your mail</span></div>
      <div className="commit-list" id="commitList">
        {!shown.length && <div className="commit-empty">No open commitments — you’re on top of it.</div>}
        {shown.map((it) => {
          const level = Math.max(1, Math.min(5, it.importance || 1));
          const link = typeof it.link === "string" && it.link.startsWith("https://") ? it.link : null;
          return (
            <div className="commit-row" key={it.pk} data-pk={it.pk}>
              <button className="cm-check" title="Done — archive" aria-label="Done" onClick={() => m.file({ pk: it.pk }, "archive")}>✓</button>
              <button className="cm-main" onClick={() => { close(); m.openAny(it.pk); }}>
                <div className="cm-action">{it.action}</div>
                <div className="cm-meta"><span className="who">{it.from_name || it.from_addr || "—"}</span> · {(it.subject || "(no subject)").slice(0, 64)} · p{level}</div>
              </button>
              <div className="cm-side">
                <button title="Later — snooze to tomorrow" aria-label="Later — snooze to tomorrow" onClick={() => m.snooze({ pk: it.pk }, { preset: "tomorrow" }, [it.pk])}><Icon name="clock" /></button>
                {link && <button title="Open original" aria-label="Open original" onClick={() => window.open(link, "_blank", "noopener")}><Icon name="external" /></button>}
              </div>
            </div>
          );
        })}
      </div>
      <div className="commit-hint">click a to-do to open it · ✓ done (archive) · <Icon name="clock" /> later · <Icon name="external" /> open original · Esc to close</div>
    </>
  );
}

function Stat({ n, label, hot }: { n: number; label: string; hot?: boolean }) {
  return <div className={hot ? "rv-stat hot" : "rv-stat"}><div className="n">{n}</div><div className="l">{label}</div></div>;
}

function ReviewBody({ d, m, again }: { d: Review; m: Mailbox; again: () => void }) {
  const s = d.stats;
  const open = (pk: number | null, account: string, thread: string | null | undefined) => {
    if (pk === null) return;
    close();
    m.openNamed(pk, account, thread);
  };
  return (
    <>
      <div className="rv-eyebrow"><Icon name="wind" /> Your week</div>
      <p className="rv-narr">{d.narrative}</p>
      <div className="rv-stats">
        <Stat n={s?.sent ?? 0} label="sent" />
        <Stat n={s?.received ?? 0} label="received" />
        <Stat n={s?.needs_you ?? 0} label="need you" hot={(s?.needs_you ?? 0) > 0} />
        <Stat n={s?.waiting_on ?? 0} label="awaiting reply" />
        {s?.reply_later ? <Stat n={s.reply_later} label="reply-later" /> : null}
        {s?.cleaned ? <Stat n={s.cleaned} label="cleaned up" /> : null}
      </div>
      {(d.next?.length ?? 0) > 0 && <div className="rv-sec next"><Icon name="wind" /> Start next week with</div>}
      {d.next?.map((t) => <div className="rv-item" key={t}><span className="ic">→</span><div className="body"><div className="t">{t}</div></div></div>)}
      {(d.needs?.length ?? 0) > 0
        ? <div className="rv-sec">Still needs you ({d.needs!.length})</div>
        : <><div className="rv-sec">Still needs you</div><div className="rv-empty">Nothing important is unanswered.</div></>}
      {d.needs?.map((n) => (
        <button className="rv-item need" key={n.pk} onClick={() => open(n.pk, n.account, n.thread_id)}>
          <span className="ic">●</span>
          <div className="body"><div className="t">{n.subject}</div><div className="m"><span className="who">{n.who}</span>{n.action ? ` · ${n.action}` : ""}</div></div>
        </button>
      ))}
      {(d.waiting?.length ?? 0) > 0 && <div className="rv-sec">You’re waiting on a reply</div>}
      {d.waiting?.map((w, i) => (
        <button className="rv-item wait" key={w.pk ?? `w${i}`} disabled={w.pk === null} onClick={() => open(w.pk, w.account, w.thread_id)}>
          <span className="ic">◷</span>
          <div className="body"><div className="t">{w.subject}</div><div className="m">to {w.to || "—"}</div></div>
        </button>
      ))}
      <div className="rv-foot">
        {d.model === "fallback" ? "summary from your data" : d.model || "claude"}
        {d.generated ? ` · ${d.generated.slice(0, 10)}` : ""}{d.cached ? " · cached" : ""}
        {d.stale && <> · <span className="ai-stale">mail changed — <button className="ai-rerun" onClick={again}>refresh</button></span></>}
      </div>
    </>
  );
}

function YourWeek({ m }: { m: Mailbox }) {
  const { review, loading, error, again } = useReview(true);
  let body: React.ReactNode = null;
  if (loading) body = <div className="rv-load"><span className="spin">↻</span> Looking back over your week…</div>;
  else if (error instanceof ApiError && error.aiOff) body = <div className="rv-err">AI is off, so there is no review: Claude writes it, and MailMind has no Anthropic API key.</div>;
  else if (error instanceof ApiError && error.overLimit) body = <div className="rv-err">Today’s AI limit is reached, so your review isn’t written. <button className="ai-rerun" onClick={again}>Write it</button></div>;
  else if (error) body = <div className="rv-err">Couldn’t build your review. <button className="ai-rerun" onClick={again}>Retry</button></div>;
  else if (review && !review.ok) body = <div className="rv-err">{(review as { error?: string }).error || "Review unavailable"} <button className="ai-rerun" onClick={again}>Retry</button></div>;
  else if (review) body = <ReviewBody d={review} m={m} again={again} />;
  return (
    <>
      <div className="commit-hd">
        <Icon name="cal" /> Your week <span className="sub" id="rvRange">{review?.ok ? "· last 7 days" : ""}</span>
        <button className="ai-rerun" id="rvRerun" title="Regenerate" aria-label="Regenerate" style={{ marginLeft: 8 }} onClick={again}>↻</button>
      </div>
      <div className="rv-scroll" id="rvBody">{body}</div>
      <div className="commit-hint">click an item to open it · ↻ regenerate · Esc to close</div>
    </>
  );
}

export function Panels({ m }: { m: Mailbox }) {
  const open = panel.useValue();
  useLayer(open !== null, () => panel.set(null));
  return (
    <>
      <Overlay open={open === "commits"} label="Commitments"><Commitments m={m} /></Overlay>
      <Overlay open={open === "week"} label="Weekly review" wide><YourWeek m={m} /></Overlay>
    </>
  );
}

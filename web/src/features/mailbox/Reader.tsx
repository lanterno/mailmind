// The reader pane: one message, or a whole conversation with its latest open.
import { useEffect, useState, type ReactNode } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { ApiError, post } from "../../lib/api/client";
import type { GetAnswers } from "../../lib/api/types";
import { BriefPane } from "../assistant";
import { composing, myAddresses, answer, forget, useDraftAhead, useDraftChip, canQuickReply, QUICK } from "../compose";
import { createStore } from "../../lib/store";
import { fileSize } from "../../lib/text";
import { toast } from "../../lib/toast";
import { Icon } from "../../lib/ui/Icon";
import { armDwell, cancelDwell, markSeen } from "./actions";
import { AiCard, summarize } from "./AiCard";
import { useBody, useThread, type Opened } from "./data";
import { MailFrame, type MailBody } from "./MailFrame";
import { MoreMenu } from "./MoreMenu";
import { ReadMenu } from "./ReadMenu";
import { snoozeAsk } from "../attention";
import type { Mailbox } from "./useMailbox";
import { SenderButton } from "../discovery";

type Body = Extract<Opened, { ok: true }>;

export const readFocus = createStore(false);
const narrow = () => matchMedia("(max-width:820px)").matches;

export function setReadFocus(on: boolean) {
  const next = on && !narrow();     // a phone already shows one pane at a time
  readFocus.set(next);
  document.body.classList.toggle("read-focus", next);
}

function Cab({ ca, thread, icon, label, title, onClick, danger, on, children }: { ca: string; thread: boolean; icon: string; label: string; title?: string; onClick: (e: React.MouseEvent<HTMLButtonElement>) => void; danger?: boolean; on?: boolean; children?: ReactNode }) {
  const verb = thread ? { "data-tca": ca } : { "data-ca": ca };
  return (
    <button {...verb} className={`cab${danger ? " cab-del" : ""}${on ? " on" : ""}`} title={title ?? label} aria-label={label} onClick={onClick}>
      <span><Icon name={icon} className={on ? "fill" : undefined} /></span> {children ?? label}
    </button>
  );
}

function ReaderNav({ m }: { m: Mailbox }) {
  const focusing = readFocus.useValue();
  const i = m.rows.findIndex((r) => r.pk === m.openPk), n = m.rows.length;
  return (
    <span className="rd-nav">
      <button className="cab rd-focus" aria-pressed={focusing} title="Focus reading — hide folders and list (↵)" aria-label="Focus reading" onClick={() => setReadFocus(!focusing)}><span><Icon name="expand" /></span></button>
      {i >= 0 && <span className="rd-pos">{i + 1} of {n}{m.hasMore ? "+" : ""}</span>}
      <button className="cab" title="Previous (k)" aria-label="Previous message" disabled={i <= 0} onClick={() => m.move(-1)}><span><Icon name="up" /></span></button>
      <button className="cab" title="Next (j)" aria-label="Next message" disabled={i < 0 || i >= n - 1} onClick={() => m.move(1)}><span><Icon name="down" /></span></button>
    </span>
  );
}

// Reply says which of three it is: a draft written ahead, one being written, or just Reply.
function Answering({ pk, thread, drafts }: { pk: number | null; thread: boolean; drafts: boolean }) {
  const chip = useDraftChip(pk);
  if (pk === null) return null;
  const verb = (ca: string) => (thread ? { "data-tca": ca } : { "data-ca": ca });
  return (
    <>
      {chip === "ready"
        ? <button {...verb("draftready")} className="cab cab-draft" title="AI pre-drafted a reply — opens in the composer for review; nothing sends until you do (r)" onClick={() => void answer(pk)}><span><Icon name="wind" /></span> Draft ready</button>
        : drafts && chip === "drafting"
          ? <button {...verb("reply")} className="cab cab-drafting" title="AI is drafting a reply in the background — click for a plain reply now" onClick={() => { forget(pk); void composing.reply(pk); }}><span><Icon name="wind" /></span> Drafting…</button>
          : <button {...verb("reply")} className="cab" title="Reply (r)" onClick={() => void composing.reply(pk)}><span><Icon name="reply" /></span> Reply</button>}
      <Cab ca="replyall" thread={thread} icon="replyall" label="Reply all" title="Reply all (a)" onClick={() => void composing.reply(pk, true)}>All</Cab>
      <Cab ca="forward" thread={thread} icon="forward" label="Forward" title="Forward (f)" onClick={() => void composing.forward(pk)} />
    </>
  );
}

function QuickReplies({ pk }: { pk: number }) {
  return (
    <div className="qr-row" role="group" aria-label="Quick replies">
      <span className="qr-k"><Icon name="reply" />Quick reply</span>
      {QUICK.map((q) => <button key={q.key} className="qr" data-qr={q.key} onClick={() => void composing.quickReply(pk, q.key)}>{q.chip}</button>)}
    </div>
  );
}

const SAID: Record<string, string> = { accept: "Accepted ✓", tentative: "Tentative ?", decline: "Declined ✕" };

function Rsvp({ pk }: { pk: number }) {
  const [said, setSaid] = useState<{ done: boolean; text: string } | null>(null);
  const reply = async (decision: string) => {
    setSaid({ done: false, text: "Sending RSVP…" });
    try {
      const r = await post("/api/invite/rsvp", { pk, decision }) as { ok: boolean; error?: string; organizer?: string; outbox_id?: number };
      if (!r.ok) {
        setSaid({ done: false, text: `Couldn’t RSVP: ${r.error ?? "error"}` });
        toast(r.error ?? "RSVP failed");
        return;
      }
      setSaid({ done: true, text: `${SAID[decision]} — reply sent to ${r.organizer || "organizer"}` });
      toast(`RSVP: ${SAID[decision]}`);
      if (r.outbox_id) composing.queued(r.outbox_id, "RSVP");
    } catch (e) {
      setSaid({ done: false, text: e instanceof ApiError ? `Couldn’t RSVP: ${e.message}` : "Couldn’t RSVP" });
      toast("RSVP failed");
    }
  };
  return (
    <div className="inv-rsvp" data-rsvp-pk={pk}>
      {said
        ? <span className={said.done ? "inv-done" : "inv-sending"}>{said.text}</span>
        : (
          <>
            <button className="inv-yes" data-rsvp="accept" onClick={() => void reply("accept")}>✓ Accept</button>
            <button className="inv-maybe" data-rsvp="tentative" onClick={() => void reply("tentative")}>? Maybe</button>
            <button className="inv-no" data-rsvp="decline" onClick={() => void reply("decline")}>✕ Decline</button>
          </>
        )}
    </div>
  );
}

// The verbs both views share, ahead of filing.
function Triage({ m, thread }: { m: Mailbox; thread: boolean }) {
  const target = m.target();
  const later = m.folder === "__replylater__", muted = m.folder === "__muted__", waiting = m.folder === "__waiting__";
  return (
    <>
      <Cab ca="summarize" thread={thread} icon="wind" label="Summarize" title="AI summary (i)" onClick={() => { if (target) void summarize(target); }} />
      <Cab ca="snooze" thread={thread} icon="clock" label="Snooze" title="Snooze (h)" onClick={(e) => { if (target && m.openPk !== null) snoozeAsk.set({ target, rows: [m.openPk], anchor: e.currentTarget, canUnsnooze: m.folder === "__snoozed__" }); }} />
      {later
        ? <Cab ca="rldone" thread={thread} icon="check" label="Done" title="Done — remove from Reply later" onClick={m.replyLaterDone} />
        : <Cab ca="replylater" thread={thread} icon="bookmark" label="Reply later" title="Reply later (l)" onClick={m.replyLater}>Later</Cab>}
      {muted
        ? <Cab ca="unmute" thread={thread} icon="bell-off" label="Unmute" title="Unmute thread (w)" onClick={m.unmute} />
        : <Cab ca="mute" thread={thread} icon="bell-off" label="Mute" title="Mute thread (w)" onClick={m.mute} />}
      {waiting && <Cab ca="dismiss" thread={thread} icon="check" label="Done" title="Dismiss follow-up" onClick={m.dismissFollowup} />}
    </>
  );
}

function UpNext({ m, canArchive }: { m: Mailbox; canArchive: boolean }) {
  const i = m.rows.findIndex((r) => r.pk === m.openPk);
  if (i < 0) return null;
  const next = m.rows[i + 1];
  if (!next) return <div className="rd-next end">{m.hasMore ? "That’s the last one loaded — “Load more” at the end of the list keeps going." : "That’s the last one in this list."}</div>;
  return (
    <div className="rd-next">
      <div className="rdn-t">
        <span className="rdn-k">Up next</span>
        <span className="rdn-who">{next.fromName || next.fromAddr}</span>
        <span className="rdn-sj">{next.subject || "(no subject)"}</span>
      </div>
      {canArchive && <button className="cab" title="Archive this one and open the next (e)" onClick={() => m.fileOpen("archive")}><span><Icon name="archive" /></span> Archive &amp; next</button>}
      <button className="cab" title="Next (j)" onClick={() => m.move(1)}><span>↓</span> Next</button>
    </div>
  );
}

function ExternalBar({ d, blocked }: { d: Body; blocked: number }) {
  const domain = (d.from_addr ?? "").split("@")[1]?.toLowerCase();
  return (
    <div className="rd-extbar">
      <span className="ext">External{domain ? ` · ${domain}` : ""}</span>
      <span className="shield"><Icon name="shield" /> Sandboxed{blocked ? ` · ${blocked} tracker${blocked > 1 ? "s" : ""} blocked` : " · no active content"}</span>
    </div>
  );
}

function Invite({ d }: { d: Body }) {
  const v = d.invite;
  if (!v) return null;
  const kinds: Record<string, string> = { REQUEST: "Invitation", REPLY: "RSVP", CANCEL: "Cancelled", COUNTER: "Counter-proposal" };
  const attendees = v.attendees ?? [];
  const names = attendees.slice(0, 8).map((a) => a.name || a.email).join(", ");
  return (
    <div className="invite-card">
      <div className="inv-hd"><Icon name="cal" /> {kinds[v.method] ?? "Event"}{v.status && v.status !== "CONFIRMED" ? ` · ${v.status.toLowerCase()}` : ""}</div>
      <div className="inv-title">{v.summary}</div>
      <div className="inv-line"><Icon name="clock" /> {v.when}{v.recurrence ? ` · ${v.recurrence}` : ""}</div>
      {v.location && <div className="inv-line"><Icon name="pin" /> {v.location}</div>}
      {v.organizer?.email && <div className="inv-line inv-sub">organizer: {v.organizer.name || v.organizer.email}</div>}
      {names && <div className="inv-line inv-sub">{attendees.length} attendee{attendees.length === 1 ? "" : "s"}: {names}{attendees.length > 8 ? ` +${attendees.length - 8}` : ""}</div>}
      {(v.method === "REQUEST" || !v.method) && v.organizer?.email && <Rsvp pk={d.pk!} />}
      {d.invite_part != null && <div className="inv-actions"><a className="inv-dl" href={`/api/message/${d.pk}/attachment/${encodeURIComponent(d.invite_part)}`} download="invite.ics">＋ Add to calendar</a></div>}
    </div>
  );
}

function Attachments({ d }: { d: Body }) {
  const files = (d.attachments ?? []).filter((a) => !(a.is_inline && a.content_id));
  if (!files.length) return null;
  return (
    <div className="rd-attach">
      {files.map((a) => (
        <a key={a.part_id} className="rd-att" href={`/api/message/${d.pk}/attachment/${encodeURIComponent(a.part_id)}`} download={a.filename} title={`Download ${a.filename}`}>
          <Icon name="clip" /> <span className="fn">{a.filename}</span> <span className="sz">{fileSize(a.size)}</span> <span className="dl"><Icon name="download" /></span>
        </a>
      ))}
    </div>
  );
}

// What stands around a body in either view, then the body. Asking for images loads its frame again with them, and nothing else.
// A body can take a while: the first open of an older message goes to the server.
function useSlow() {
  const [slow, setSlow] = useState(false);
  useEffect(() => {
    const t = setTimeout(() => setSlow(true), 4000);
    return () => clearTimeout(t);
  }, []);
  return slow;
}

function SlowWords({ first, then }: { first: string; then: string }) {
  const slow = useSlow();
  return <div className="nores" role="status"><span className="spin">↻</span> {slow ? then : first}</div>;
}

function Fetching({ whole }: { whole: boolean }) {
  const slow = useSlow();
  return (
    <>
      <div className="rd-skel" role="status" aria-label="Loading"><div className="skb" /><div className="skb" /><div className="skb short" /><div className="skb" /><div className="skb" /><div className="skb short" /></div>
      <div className="rd-skelhint" id="rdSkelHint">{slow && <><span className="spin">↻</span> {whole ? "Still fetching from the server — the first open of an older message can take up to a minute." : "Still fetching this message…"}</>}</div>
    </>
  );
}

function MailContent({ pk, minHeight, pad, whole = false }: { pk: number; minHeight: number; pad: number; whole?: boolean }) {
  const [images, setImages] = useState(false);
  const { data: d, isError, refetch } = useBody(pk);
  if (isError) return <div className="nores">Couldn’t load. <button className="ai-rerun" id="rdRetry" onClick={() => void refetch()}>Retry</button></div>;
  if (!d) return <Fetching whole={whole} />;
  if (!d.ok) return <div className="nores">{d.error ?? "error"}</div>;
  const blocked = images ? 0 : (d.blocked_images ?? 0) + (d.blocked_css_urls ?? 0);
  return (
    <>
      <ExternalBar d={d} blocked={blocked} />
      <Invite d={d} />
      {blocked > 0 && (
        <div className="rd-imgbar">
          <Icon name="shield" /> {blocked} remote image{blocked > 1 ? "s" : ""} hidden — loading one can let the sender know you opened this email{" "}
          <button onClick={() => setImages(true)}>Load images</button>
        </div>
      )}
      <Attachments d={d} />
      <MailFrame body={d as MailBody} images={images} minHeight={minHeight} pad={pad} />
    </>
  );
}

function MessageView({ pk, m }: { pk: number; m: Mailbox }) {
  const { data: d } = useBody(pk);
  const row = m.openRow;
  useEffect(() => {
    if (row?.seen === false) armDwell(m.qc, pk);
    return cancelDwell;
  }, [m.qc, pk, row?.seen]);
  const flagged = row?.flagged ?? false;
  const isDraft = Boolean(d?.ok && d.is_draft);
  useDraftAhead(d ? pk : null, row?.replyWorthy ?? false, isDraft || m.folder === "__muted__");
  const quick = d?.ok && !isDraft && canQuickReply(d.from_addr, myAddresses(m.digest), row);
  return (
    <>
      <div className="rd-actions">
        <button className="cab rd-back" title="Back to list" onClick={m.close}><span><Icon name="left" /></span> List</button>
        {isDraft
          ? <Cab ca="edit" thread={false} icon="pen" label="Edit" title="Edit draft" onClick={() => void composing.editDraft(pk)} />
          : <><Answering pk={pk} thread={false} drafts /><Triage m={m} thread={false} /></>}
        <span className="rd-sp" aria-hidden="true" />
        {!isDraft && <Cab ca="archive" thread={false} icon="archive" label="Archive" title="Archive (e)" onClick={() => m.fileOpen("archive")} />}
        <Cab ca="trash" thread={false} icon="trash" label="Delete" title="Delete (d / ⌫ / #)" danger onClick={() => m.fileOpen("trash")} />
        {!isDraft && (
          <>
            <Cab ca="junk" thread={false} icon="ban" label="Junk" title="Mark junk (b)" onClick={() => m.fileOpen("junk")} />
            <Cab ca="star" thread={false} icon="star" label={flagged ? "Starred" : "Star"} title="Star (s)" on={flagged} onClick={m.star} />
            <Cab ca="unread" thread={false} icon="mail" label="Unread" title="Mark unread (u)" onClick={m.unread} />
            <ReadMenu />
            <MoreMenu />
          </>
        )}
        <ReaderNav m={m} />
      </div>
      {!d && row && (
        <>
          <div className="rd-top"><span className="rd-tag">{m.title}</span></div>
          <div className="rd-subj">{row.subject}</div>
          <div className="rd-from"><span className="nm">{row.fromName}</span> <span className="ad">{row.fromAddr}</span></div>
        </>
      )}
      {d?.ok && (
        <>
          <div className="rd-top"><span className="rd-tag">{m.title}</span><span className="rd-when">{d.date}</span></div>
          <div className="rd-subj">{d.subject || "(no subject)"}</div>
          <div className="rd-from">
            <SenderButton name={d.from_name ?? ""} addr={d.from_addr ?? ""} m={m} />
            {d.to ? ` · to ${d.to.slice(0, 80)}` : ""}
          </div>
        </>
      )}
      <AiCard target={m.target()} row={row} />
      <MailContent key={pk} pk={pk} minHeight={400} pad={30} whole />
      {quick && <QuickReplies pk={pk} />}
      <UpNext m={m} canArchive={!isDraft} />
    </>
  );
}

function Member({ m, startsOpen }: { m: Extract<GetAnswers["/api/thread"], { ok: true }>["items"][number]; startsOpen: boolean }) {
  const qc = useQueryClient();
  const [open, setOpen] = useState(startsOpen);
  const [wasOpened, setWasOpened] = useState(startsOpen);
  useEffect(() => {
    if (open && !m.seen) void markSeen(qc, m.pk);
  }, [qc, open, m.pk, m.seen]);
  return (
    <div className={open ? "tmsg open" : "tmsg"} data-pk={m.pk}>
      <button
        className="tmsg-hd"
        aria-expanded={open}
        onClick={() => {
          setOpen(!open);
          setWasOpened(true);
        }}
      >
        <span className="tnm">{m.from_name}</span>
        {m.flagged && <span className="rflag" style={{ color: "var(--gold)" }}><Icon name="star" className="fill" /></span>}
        {m.has_attach && <span className="rflag" style={{ color: "var(--mute)" }}><Icon name="clip" /></span>}
        {m.answered && <span className="tans">↩</span>}
        <span className="tfolder">{m.folder}</span><span className="twhen">{m.when}</span>
        <span className="tsnip">{m.snippet}</span>
      </button>
      {/* Kept once opened, so closing and opening again doesn't reload the frame. */}
      <div className="tmsg-body">{wasOpened && <MailContent pk={m.pk} minHeight={300} pad={24} />}</div>
    </div>
  );
}

function ConversationView({ opened, m }: { opened: { id: string; account: string }; m: Mailbox }) {
  const { data: d, isError } = useThread(opened.account, opened.id);
  const items = d?.ok ? d.items : [];
  const replyPk = d?.ok ? d.reply_pk : null;
  const latest = items.find((x) => x.pk === replyPk);
  useDraftAhead(replyPk, false, m.folder === "__muted__");
  const quick = latest && canQuickReply(latest.from_addr, myAddresses(m.digest), { isJunk: Boolean(latest.is_junk), bucket: m.openRow?.bucket });
  return (
    <>
      <div className="rd-actions">
        <button className="cab rd-back" title="Back to list" onClick={m.close}><span><Icon name="left" /></span> List</button>
        <Answering pk={replyPk} thread drafts={false} />
        <Triage m={m} thread />
        <span className="rd-sp" aria-hidden="true" />
        <Cab ca="archive" thread icon="archive" label="Archive conversation" title="Archive conversation (e)" onClick={() => m.fileOpen("archive")}>Archive</Cab>
        <Cab ca="trash" thread icon="trash" label="Delete conversation" title="Delete conversation (d / ⌫ / #)" danger onClick={() => m.fileOpen("trash")}>Delete</Cab>
        <ReadMenu />
        <MoreMenu />
        <ReaderNav m={m} />
      </div>
      {isError && <div className="nores">Couldn’t load.</div>}
      {d && !d.ok && <div className="nores">error</div>}
      {d?.ok && items.length > 0 && (
        <>
          <div className="rd-subj">{items[0]!.subject || "(no subject)"} <span className="thr-n">{d.count} message{d.count === 1 ? "" : "s"}</span></div>
          <AiCard target={m.target()} row={m.openRow} />
          <div className="thread" id="thread">
            {items.map((x, i) => <Member key={x.pk} m={x} startsOpen={i === items.length - 1} />)}
          </div>
          {quick && <QuickReplies pk={replyPk!} />}
          <UpNext m={m} canArchive />
        </>
      )}
      {!d && !isError && <SlowWords first="Loading conversation…" then="Still fetching — the first open can be slow." />}
    </>
  );
}

export function Reader({ m }: { m: Mailbox }) {
  const { opened } = m;
  if (!opened && m.inInbox && !m.searching) return <section className="reader brief-pane" id="reader"><BriefPane m={m} /></section>;
  if (!opened) {
    const reading = m.list.data?.pages.at(-1)?.syncing;
    return <section className="reader empty-pane" id="reader">{m.all.length ? "No message selected." : m.list.isPending ? "" : reading ? "Reading this folder for the first time…" : "No messages in this folder."}</section>;
  }
  return (
    <section className="reader" id="reader" aria-label="Message">
      {opened.kind === "message"
        ? <MessageView key={opened.pk} pk={opened.pk} m={m} />
        : <ConversationView key={opened.id} opened={opened} m={m} />}
    </section>
  );
}

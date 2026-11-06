// The reader pane: one message, or a whole conversation with its latest open.
import { useEffect, useState, type ReactNode } from "react";
import { useQueryClient } from "@tanstack/react-query";
import type { GetAnswers } from "../../api/types.gen";
import type { Opened } from "../../app/route";
import { fileSize } from "../../lib/text";
import { Icon } from "../../lib/ui/Icon";
import { armDwell, cancelDwell, markSeen, markUnread, setStar } from "./actions";
import { useBody, useThread } from "./data";
import { MailFrame, type MailBody } from "./MailFrame";
import type { Row } from "./rows";

type Body = GetAnswers["/api/message/{pk}/body"];

function Cab({ icon, label, title, onClick, danger, on, children }: { icon: string; label: string; title?: string; onClick: () => void; danger?: boolean; on?: boolean; children?: ReactNode }) {
  return (
    <button className={`cab${danger ? " cab-del" : ""}${on ? " on" : ""}`} title={title ?? label} aria-label={label} onClick={onClick}>
      <span><Icon name={icon} className={on ? "fill" : undefined} /></span> {children ?? label}
    </button>
  );
}

function ExternalBar({ d }: { d: Body }) {
  const blocked = (d.blocked_images ?? 0) + (d.blocked_css_urls ?? 0);
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

// What stands around a body in either view, then the body. Asking for images fetches it again with them.
function MailContent({ pk, minHeight, pad }: { pk: number; minHeight: number; pad: number }) {
  const [images, setImages] = useState(false);
  const { data: d, isError } = useBody(pk, images);
  if (isError) return <div className="nores">Couldn’t load.</div>;
  if (!d) return <div className="nores" role="status">Loading…</div>;
  if (!d.ok) return <div className="nores">{d.error ?? "error"}</div>;
  const blocked = (d.blocked_images ?? 0) + (d.blocked_css_urls ?? 0);
  return (
    <>
      <ExternalBar d={d} />
      <Invite d={d} />
      {blocked > 0 && (
        <div className="rd-imgbar">
          <Icon name="shield" /> {blocked} remote image{blocked > 1 ? "s" : ""} hidden — loading one can let the sender know you opened this email{" "}
          <button onClick={() => setImages(true)}>Load images</button>
        </div>
      )}
      <Attachments d={d} />
      <MailFrame body={d as MailBody} minHeight={minHeight} pad={pad} />
    </>
  );
}

interface VerbProps { row: Row | undefined; onFile: (kind: "archive" | "trash" | "junk") => void; onBack: () => void }

function MessageView({ pk, row, onFile, onBack, folderTag }: VerbProps & { pk: number; folderTag: string }) {
  const qc = useQueryClient();
  const { data: d } = useBody(pk, false);
  useEffect(() => {
    if (row?.seen === false) armDwell(qc, pk);
    return cancelDwell;
  }, [qc, pk, row?.seen]);
  const flagged = row?.flagged ?? false;
  return (
    <>
      <div className="rd-actions">
        <button className="cab rd-back" title="Back to list" onClick={onBack}><span><Icon name="left" /></span> List</button>
        <span className="rd-sp" aria-hidden="true" />
        <Cab icon="archive" label="Archive" title="Archive (e)" onClick={() => onFile("archive")} />
        <Cab icon="trash" label="Delete" title="Delete (d / #)" danger onClick={() => onFile("trash")} />
        <Cab icon="ban" label="Junk" title="Mark junk (b)" onClick={() => onFile("junk")} />
        {row && <Cab icon="star" label={flagged ? "Starred" : "Star"} title="Star (s)" on={flagged} onClick={() => void setStar(qc, row)} />}
        <Cab icon="mail" label="Unread" title="Mark unread (u)" onClick={() => void markUnread(qc, pk)} />
      </div>
      {d?.ok && (
        <>
          <div className="rd-top"><span className="rd-tag">{folderTag}</span><span className="rd-when">{d.date}</span></div>
          <div className="rd-subj">{d.subject || "(no subject)"}</div>
          <div className="rd-from">
            <span className="rd-sender"><span className="nm">{d.from_name}</span> <span className="ad">{d.from_addr}</span></span>
            {d.to ? ` · to ${d.to.slice(0, 80)}` : ""}
          </div>
        </>
      )}
      <MailContent key={pk} pk={pk} minHeight={400} pad={30} />
    </>
  );
}

function Member({ m, startsOpen }: { m: GetAnswers["/api/thread"]["items"][number]; startsOpen: boolean }) {
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

function ConversationView({ opened, onFile, onBack }: VerbProps & { opened: Extract<Opened, { kind: "thread" }> }) {
  const { data: d, isError } = useThread(opened.account, opened.id);
  const items = d?.items ?? [];
  return (
    <>
      <div className="rd-actions">
        <button className="cab rd-back" title="Back to list" onClick={onBack}><span><Icon name="left" /></span> List</button>
        <span className="rd-sp" aria-hidden="true" />
        <Cab icon="archive" label="Archive conversation" title="Archive conversation (e)" onClick={() => onFile("archive")}>Archive</Cab>
        <Cab icon="trash" label="Delete conversation" title="Delete conversation (d / #)" danger onClick={() => onFile("trash")}>Delete</Cab>
      </div>
      {isError && <div className="nores">Couldn’t load.</div>}
      {d && !d.ok && <div className="nores">error</div>}
      {items.length > 0 && (
        <>
          <div className="rd-subj">{items[0]!.subject || "(no subject)"} <span className="thr-n">{d!.count} messages</span></div>
          <div className="thread" id="thread">
            {items.map((m, i) => <Member key={m.pk} m={m} startsOpen={i === items.length - 1} />)}
          </div>
        </>
      )}
      {!d && !isError && <div className="nores" role="status">Loading conversation…</div>}
    </>
  );
}

export function Reader({ opened, ...rest }: VerbProps & { opened: Opened; folderTag: string }) {
  if (!opened) return <section className="reader empty-pane" id="reader">Select a message to read its summary.</section>;
  return (
    <section className="reader" id="reader" aria-label="Message">
      {opened.kind === "message"
        ? <MessageView key={opened.pk} pk={opened.pk} {...rest} />
        : <ConversationView key={opened.id} opened={opened} {...rest} />}
    </section>
  );
}

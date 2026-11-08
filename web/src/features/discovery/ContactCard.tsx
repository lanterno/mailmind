// Who this is: how much they've written, whether you answer, their latest, and two ways on from here.
import { useRef, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Dialog, Popover } from "react-aria-components";
import { ApiError, get, post } from "../../lib/api/client";
import { go } from "../../app/route";
import { copyId, copyOf } from "../../lib/copy";
import { askOverLimit } from "../../lib/overLimit";
import { avatarHue, shortAddress } from "../../lib/text";
import { Icon } from "../../lib/ui/Icon";
import { useFocusFirst } from "../../lib/ui/menuKeys";
import { composing } from "../compose";
import type { Mailbox } from "../mailbox";
import { senderStats, type SenderFacts } from "./model";

// A sender's initial, in the colour their address always gets.
function Monogram({ name, addr, className }: { name: string; addr: string; className: string }) {
  return <span className={className} aria-hidden="true" style={{ color: `hsl(${avatarHue(addr)} var(--mono-s) var(--mono-l))` }}>{(name || addr || "?")[0]!.toUpperCase()}</span>;
}

// What the AI can say of a sender's recent threads, on a click, and nothing without one.
function Summarize({ addr }: { addr: string }) {
  const ask = useMutation({ mutationFn: () => post("/api/contact/summary", { addr }, { askOverLimit }) });
  if (ask.isPending) return <div className="cc-sum" role="status"><span className="spin"><Icon name="wind" /></span> Reading their recent threads…</div>;
  if (ask.data) {
    const d = ask.data;
    return (
      <div className="cc-sum">
        <p>{d.summary}</p>
        {d.topics.length > 0 && <div className="cc-topics">{d.topics.map((t, i) => <span className="flood-chip" key={i}>{t}</span>)}</div>}
        {d.open && <p className="cc-open"><b>Still with you:</b> {d.open}</p>}
      </div>
    );
  }
  const off = ask.error instanceof ApiError && ask.error.aiOff;
  return (
    <div className="cc-sum">
      {ask.error ? <span className="cc-sumnote">{off ? "AI is off." : "Couldn’t summarize this sender."}</span> : null}
      {!off && <button className="cc-sumgo" onClick={() => ask.mutate()}><Icon name="wind" /> {ask.error ? "Try again" : "Summarize this sender"}</button>}
    </div>
  );
}

function Card({ addr, m, close }: { addr: string; m: Mailbox; close: () => void }) {
  const inside = useFocusFirst<HTMLDivElement>("button");
  const { data: d, isError } = useQuery({ queryKey: ["contact", addr], queryFn: () => get(`/api/contact?addr=${encodeURIComponent(addr)}`) });
  if (isError) return <div className="cc-load">Couldn’t load this sender.</div>;
  if (!d) return <div className="cc-load" role="status">Loading…</div>;
  const stats = [senderStats(d), d.last_ago ? `last ${d.last_ago} ago` : ""].filter(Boolean).join(" · ");
  const accounts = d.accounts ?? [], recent = d.recent ?? [];
  return (
    <div ref={inside}>
      <div className="cc-hd">
        <Monogram name={d.name} addr={d.addr} className="cc-mono" />
        <div><div className="cc-name">{d.name || d.addr}</div><div className="cc-addr">{d.addr}</div></div>
      </div>
      <div className="cc-stats">{stats}</div>
      {accounts.length > 1 && <div className="cc-accts">writes to {accounts.map((a) => m.aliases[a] || shortAddress(a)).join(", ")}</div>}
      {recent.length > 0 && (
        <div className="cc-recent">
          {recent.map((x) => (
            <button className="cc-thread" key={copyId(x)} onClick={() => { close(); m.openNamed(copyOf(x), x.account, x.thread_id); }}>
              <span>{x.subject}</span><span className="cc-when">{x.when}</span>
            </button>
          ))}
        </div>
      )}
      <Summarize addr={d.addr} />
      <div className="cc-actions">
        <button onClick={() => { close(); void composing.open({ mode: "new", to: [d.addr] }); }}><Icon name="pen" /> Compose</button>
        <button onClick={() => { close(); go({ source: { kind: "search", q: `from:${d.addr}`, account: "" }, opened: null }); }}><Icon name="search" /> From this sender</button>
      </div>
    </div>
  );
}

// Who wrote this, with what their card opens with beside it. `facts` come with the message, so showing them asks nothing of the
// server, and only a click on the name asks for the card itself.
export function SenderButton({ name, addr, m, facts }: { name: string; addr: string; m: Mailbox; facts?: SenderFacts | null }) {
  const [open, setOpen] = useState(false);
  const button = useRef<HTMLButtonElement>(null);
  if (!addr) return <span className="rd-sender"><span className="nm">{name}</span></span>;
  return (
    <>
      <button ref={button} className="rd-sender" title="About this sender" aria-haspopup="dialog" aria-expanded={open} onClick={() => setOpen(true)}>
        <Monogram name={name} addr={addr} className="rd-mono" /><span className="nm">{name}</span> <span className="ad">{addr}</span>
      </button>
      {facts && <span className="rd-facts">{senderStats(facts)}</span>}
      <Popover className="contact-card rac" isOpen={open} triggerRef={button} placement="bottom start" offset={6} onOpenChange={setOpen}>
        <Dialog aria-label="About this sender"><Card addr={addr} m={m} close={() => setOpen(false)} /></Dialog>
      </Popover>
    </>
  );
}

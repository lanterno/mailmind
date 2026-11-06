// Who this is: how much they've written, whether you answer, their latest, and two ways on from here.
import { useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Dialog, Popover } from "react-aria-components";
import { get } from "../../api/client";
import { go } from "../../app/route";
import { avatarHue, shortAddress } from "../../lib/text";
import { Icon } from "../../lib/ui/Icon";
import { useFocusFirst } from "../../lib/ui/menuKeys";
import { composing } from "../compose/compose";
import type { Mailbox } from "../mailbox/useMailbox";

function Card({ addr, m, close }: { addr: string; m: Mailbox; close: () => void }) {
  const inside = useFocusFirst<HTMLDivElement>("button");
  const { data: d, isError } = useQuery({ queryKey: ["contact", addr], queryFn: () => get(`/api/contact?addr=${encodeURIComponent(addr)}`) });
  if (isError || (d && !d.ok)) return <div className="cc-load">Couldn’t load this sender.</div>;
  if (!d) return <div className="cc-load" role="status">Loading…</div>;
  const stats = [`${d.count} email${d.count === 1 ? "" : "s"}`, `replied ${d.replied}×`, d.first_ago ? `first ${d.first_ago} ago` : "", d.last_ago ? `last ${d.last_ago} ago` : ""].filter(Boolean).join(" · ");
  const accounts = d.accounts ?? [], recent = d.recent ?? [];
  return (
    <div ref={inside}>
      <div className="cc-hd">
        <span className="cc-mono" style={{ color: `hsl(${avatarHue(d.addr)} var(--mono-s) var(--mono-l))` }}>{(d.name || d.addr || "?")[0]!.toUpperCase()}</span>
        <div><div className="cc-name">{d.name || d.addr}</div><div className="cc-addr">{d.addr}</div></div>
      </div>
      <div className="cc-stats">{stats}</div>
      {accounts.length > 1 && <div className="cc-accts">writes to {accounts.map((a) => m.aliases[a] || shortAddress(a)).join(", ")}</div>}
      {recent.length > 0 && (
        <div className="cc-recent">
          {recent.map((x) => (
            <button className="cc-thread" key={x.pk} onClick={() => { close(); m.openNamed(x.pk, x.account, x.thread_id); }}>
              <span>{x.subject}</span><span className="cc-when">{x.when}</span>
            </button>
          ))}
        </div>
      )}
      <div className="cc-actions">
        <button onClick={() => { close(); void composing.open({ mode: "new", to: [d.addr] }); }}><Icon name="pen" /> Compose</button>
        <button onClick={() => { close(); go({ source: { kind: "search", q: `from:${d.addr}`, account: "" }, opened: null }); }}><Icon name="search" /> From this sender</button>
      </div>
    </div>
  );
}

export function SenderButton({ name, addr, m }: { name: string; addr: string; m: Mailbox }) {
  const [open, setOpen] = useState(false);
  const button = useRef<HTMLButtonElement>(null);
  if (!addr) return <span className="rd-sender"><span className="nm">{name}</span></span>;
  return (
    <>
      <button ref={button} className="rd-sender" title="About this sender" aria-haspopup="dialog" aria-expanded={open} onClick={() => setOpen(true)}>
        <span className="nm">{name}</span> <span className="ad">{addr}</span>
      </button>
      <Popover className="contact-card rac" isOpen={open} triggerRef={button} placement="bottom start" offset={6} onOpenChange={setOpen}>
        <Dialog aria-label="About this sender"><Card addr={addr} m={m} close={() => setOpen(false)} /></Dialog>
      </Popover>
    </>
  );
}

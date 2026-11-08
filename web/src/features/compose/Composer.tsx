// The composer: what you see of the machine in compose.ts. Each control asks the machine, and
// what it shows is the copy the machine published.
import { useEffect, useEffectEvent, useLayoutEffect, useRef, useState } from "react";
import { Float, toast } from "../../lib/toast";
import { Dialog, Modal, ModalOverlay, Popover } from "react-aria-components";
import { get } from "../../lib/api/client";
import { LIMITS } from "../../lib/api/limits.gen";
import type { ComposeMode, RewriteOp } from "../../lib/api/types";
import { entries } from "../../lib/entries";
import { fileSize, shortAddress } from "../../lib/text";
import { Icon } from "../../lib/ui/Icon";
import { useDigitKeys, useFocusFirst } from "../../lib/ui/menuKeys";
import { QuoteFrame } from "../mailbox";
import { composing, draftTone, setDraftTone } from "./compose";
import { cleanLinks } from "./linkClean";
import { DRAFT_TONES, fmtWhen, quoteLabel, rcptAddr, sendLaterPresets, type Composer, type Field } from "./model";
import { useLayer } from "../../lib/backTrap";

// A paste whose links carry tracking goes in without it, through the box's own edit so Undo still takes it back. Should the
// browser not do that, the paste goes in as it came.
function pasteClean(e: React.ClipboardEvent<HTMLTextAreaElement>) {
  const pasted = e.clipboardData.getData("text/plain"), clean = cleanLinks(pasted);
  if (clean !== pasted && document.execCommand("insertText", false, clean)) e.preventDefault();
}

const TITLES: Record<ComposeMode, string> = { new: "New message", reply: "Reply", replyall: "Reply all", forward: "Forward" };
// The server's rewrites, in the row's order and words.
const REWRITES = entries<RewriteOp, string>({ shorten: "Shorten", elaborate: "Elaborate", formal: "Formal", friendly: "Friendly", fix: "Fix grammar", translate: "Translate…" });

interface Contact { addr: string; name: string }

// A field of chips and what you're typing into it, with the addresses you've written to before offered as you go.
function ChipField({ c, field, label }: { c: Composer; field: Field; label: string }) {
  const [items, setItems] = useState<Contact[]>([]);
  const [at, setAt] = useState(-1);
  const [box, setBox] = useState<{ left: number; top: number; width: number } | null>(null);
  const input = useRef<HTMLInputElement>(null);
  const timer = useRef<ReturnType<typeof setTimeout>>(undefined);
  const close = () => {
    clearTimeout(timer.current);
    setItems([]);
    setAt(-1);
  };
  useEffect(() => () => clearTimeout(timer.current), []);
  useLayoutEffect(() => {
    if (!items.length || !input.current) return setBox(null);
    const r = input.current.getBoundingClientRect();
    setBox({ left: r.left, top: r.bottom + 3, width: Math.max(r.width, 240) });
  }, [items.length]);
  const lookUp = (value: string) => {
    clearTimeout(timer.current);
    const v = value.trim();
    if (!v) return close();
    timer.current = setTimeout(async () => {
      if (document.activeElement !== input.current) return;
      let d;
      try {
        d = await get(`/api/contacts?q=${encodeURIComponent(v)}`);
      } catch {
        return;
      }
      if (document.activeElement !== input.current) return;
      const have = new Set(c[field].map((x) => rcptAddr(x.v)));
      setItems((d.items ?? []).filter((it) => !have.has(it.addr.toLowerCase())));
      setAt(-1);
    }, 140);
  };
  const accept = (i: number) => {
    const it = items[i];
    close();
    if (it) composing.commitChip(field, it.addr);
  };
  const onKey = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (items.length) {
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        const by = e.key === "ArrowDown" ? 1 : -1;
        setAt((at + by + items.length + (at < 0 && by < 0 ? 1 : 0)) % items.length);
        return;
      }
      if (e.key === "Escape") {
        e.preventDefault();
        e.stopPropagation();   // Esc takes the list away, not the composer
        return close();
      }
      if ((e.key === "Enter" || e.key === "Tab") && at >= 0) {
        e.preventDefault();
        return accept(at);
      }
    }
    if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) return;   // ⌘Enter sends: the machine takes what's typed
    const value = e.currentTarget.value;
    if (e.key === "Enter" || e.key === "," || e.key === ";" || (e.key === "Tab" && value.trim())) {
      if (value.trim()) {
        e.preventDefault();
        composing.commitChip(field, value);
        close();
      }
    } else if (e.key === "Backspace" && !value && c[field].length) composing.removeChip(field, c[field].length - 1);
  };
  const listId = `acMenu-${field}`;
  return (
    <div className="chipfield" id={`cmp${field[0]!.toUpperCase()}${field.slice(1)}`} data-cf={field}>
      {c[field].map((x, i) => {
        const a = rcptAddr(x.v), fresh = !x.bad && composing.isNew(c, a);
        return (
          <span key={`${i}:${x.v}`} className={`cm-chip${x.bad ? " bad" : ""}${fresh ? " new" : ""}`} data-addr={a} title={fresh ? "No earlier mail with this address in MailMind" : undefined}>
            {x.v}
            {fresh && <><span className="cm-new" aria-hidden="true">new</span><span className="visually-hidden">, new to you</span></>}
            <button type="button" aria-label={`Remove ${x.v}${x.bad ? ", not a valid address" : fresh ? ", new to you" : ""}`} onClick={() => composing.removeChip(field, i)}>×</button>
          </span>
        );
      })}
      <input
        ref={input}
        className="ci"
        data-cf={field}
        value={c.typed[field]}
        placeholder={field === "to" && !c.to.length ? "name@example.com" : ""}
        aria-label={label}
        role="combobox"
        aria-autocomplete="list"
        aria-expanded={items.length > 0}
        aria-controls={items.length ? listId : undefined}
        aria-activedescendant={at >= 0 ? `${listId}-${at}` : undefined}
        autoComplete="off"
        onChange={(e) => {
          composing.setTyped(field, e.target.value);
          lookUp(e.target.value);
        }}
        onKeyDown={onKey}
        onBlur={close}
      />
      {box && items.length > 0 && (
        // eslint-disable-next-line jsx-a11y/interactive-supports-focus -- focus stays on the field, which takes the keys and names this through aria-activedescendant
        <div className="ac-menu" id={listId} role="listbox" aria-label={`Addresses for ${label}`} style={box} onMouseDown={(e) => e.preventDefault()}>
          {items.map((it, i) => (
            // eslint-disable-next-line jsx-a11y/click-events-have-key-events, jsx-a11y/interactive-supports-focus -- focus stays on the field, which takes the keys and names this through aria-activedescendant
            <div key={it.addr} id={`${listId}-${i}`} role="option" aria-selected={i === at} className={i === at ? "ac-item sel" : "ac-item"} onClick={() => accept(i)}>
              <span className="ac-nm">{it.name || it.addr}</span><span className="ac-ad">{it.addr}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function Quote({ c }: { c: Composer }) {
  const [open, setOpen] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const q = c.quote;
  if (!q || (!q.html && !q.text)) return null;
  return (
    <div className="cmp-quote" id="cmpQuote">
      <button type="button" className="cmp-qtoggle" id="cmpQToggle" aria-expanded={open} onClick={() => { setOpen(!open); setLoaded(true); }}>{quoteLabel(c, open)}</button>
      <div className="cmp-qbody" id="cmpQBody" hidden={!open}>
        {loaded && (q.html
          ? <QuoteFrame html={q.html} />
          : <div className="cmp-qtext">{q.text || "(no preview)"}</div>)}
      </div>
    </div>
  );
}

function Guard({ c }: { c: Composer }) {
  const g = c.guard;
  if (!g) return null;
  const later = g.sendAfter > 0;
  return (
    <div className="cmp-guard" id="cmpGuard" role="group" aria-labelledby="cmpGuardHd" tabIndex={-1}>
      <p className="cg-hd" id="cmpGuardHd">{later ? "Not scheduled yet" : "Not sent yet"}</p>
      <ul className="cg-list" id="cmpGuardList">
        {g.lines.map((l) => (
          <li key={l.key} className={l.soft ? "cg-line cg-soft" : "cg-line"}>
            <span>{l.text}</span>
            {l.fix && <button type="button" className="cg-fix" onClick={() => composing.guardFix(l.fix!.kind, l.field, l.fix!.swap)}>{l.fix.label}</button>}
          </li>
        ))}
      </ul>
      <div className="cg-foot">
        <span className="cg-hint" id="cmpGuardHint">{g.hard ? "Esc keeps editing" : `⌘/Ctrl+Enter ${later ? "schedules" : "sends"} anyway · Esc keeps editing`}</span>
        <button type="button" className="abtn" id="cmpGuardBack" onClick={composing.dismissGuard}>Keep editing</button>
        {!g.hard && (
          <button type="button" className={g.armed ? "abtn prim" : "abtn prim cg-arming"} id="cmpGuardGo" aria-disabled={!g.armed || undefined} aria-keyshortcuts="Meta+Enter Control+Enter" onClick={(e) => composing.confirmGuard(e.detail > 1)}>
            {later ? "Schedule anyway" : "Send anyway"}
          </button>
        )}
      </div>
    </div>
  );
}

// Mounted as it opens, so "in 1 hour" means an hour from now however long the composer has been open.
function LaterChoices({ c, pick }: { c: Composer; pick: (when: number) => void }) {
  const inside = useFocusFirst<HTMLDivElement>();
  const presets = sendLaterPresets();
  useDigitKeys(presets.length, (i) => pick(presets[i]![1]));
  return (
    <div ref={inside} className="rac-col" id="sendLaterMenu">
      {c.pendingAfter > 0 && <button onClick={() => pick(0)}><span className="snz-n">·</span> Send now instead</button>}
      {presets.map(([label, when], i) => <button key={label} onClick={() => pick(when)}><span className="snz-n">{i + 1}</span> {label}</button>)}
      <label className="snz-custom">Custom… <input
        type="datetime-local"
        id="slWhen"
        onChange={(e) => {
          if (!e.target.value) return;
          const when = Math.floor(new Date(e.target.value).getTime() / 1000);
          if (when > Date.now() / 1000) pick(when);
          else toast("Pick a future time");
        }}
      /></label>
    </div>
  );
}

function LaterMenu({ c, anchor }: { c: Composer; anchor: React.RefObject<HTMLButtonElement | null> }) {
  const open = composing.laterOpen.useValue();
  const close = () => composing.laterOpen.set(false);
  if (!open) return null;
  return (
    <Popover className="snooze-menu rac" isOpen triggerRef={anchor} placement="top start" offset={8} onOpenChange={(o) => { if (!o) close(); }}>
      <Dialog aria-label="Send later">
        <LaterChoices c={c} pick={(when) => { close(); composing.send(when); }} />
      </Dialog>
    </Popover>
  );
}

function DraftMenu({ anchor, onClose }: { anchor: React.RefObject<HTMLButtonElement | null>; onClose: () => void }) {
  const tone = draftTone.useValue();
  const [say, setSay] = useState("");
  const inside = useFocusFirst<HTMLDivElement>("input");
  const go = () => {
    onClose();
    void composing.aiDraft({ instruction: say.trim() });
  };
  return (
    <Popover className="snooze-menu draft-menu rac" isOpen triggerRef={anchor} placement="top start" offset={8} onOpenChange={(o) => { if (!o) onClose(); }}>
      <Dialog aria-label="Draft options">
        <div id="draftMenu" ref={inside}>
          <div className="dm-k" id="dmToneK">Tone</div>
          <div className="dm-tones" role="group" aria-labelledby="dmToneK">
            {entries(DRAFT_TONES).map(([k, label]) => (
              <button key={k} type="button" aria-pressed={k === tone} onClick={() => setDraftTone(k)}>{label}</button>
            ))}
          </div>
          <label className="dm-k" htmlFor="dmSay">What should it say? <small>optional</small></label>
          <input
            id="dmSay"
            maxLength={LIMITS.instruction_chars}
            autoComplete="off"
            placeholder="e.g. yes, but not before 3 pm"
            value={say}
            onChange={(e) => setSay(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {   // ⌘Enter too: it drafts here, never reaching the composer's send
                e.preventDefault();
                e.stopPropagation();
                go();
              }
            }}
          />
          <button type="button" className="dm-go" id="dmGo" onClick={go}><Icon name="wind" />Draft</button>
        </div>
      </Dialog>
    </Popover>
  );
}

function Foot({ c, ai }: { c: Composer; ai: boolean }) {
  const laterBtn = useRef<HTMLButtonElement>(null);
  const moreBtn = useRef<HTMLButtonElement>(null);
  const file = useRef<HTMLInputElement>(null);
  const [drafting, setDrafting] = useState(false);
  const g = c.guard;
  const tone = draftTone.useValue();
  const offered = ai && composing.aiOffered(c);
  let send = c.pendingAfter ? "Schedule ▸" : "Send ▸";
  let sendTitle = c.pendingAfter ? `Schedule for ${fmtWhen(c.pendingAfter)} (⌘/Ctrl+Enter)` : "Send (⌘/Ctrl+Enter)";
  if (g?.hard) sendTitle = "Fix the line above first";
  else if (g) {
    send = g.sendAfter ? "Schedule anyway ▸" : "Send anyway ▸";
    sendTitle = g.sendAfter ? `Schedule for ${fmtWhen(g.sendAfter)} anyway (⌘/Ctrl+Enter)` : "Send anyway (⌘/Ctrl+Enter)";
  }
  const holding = Boolean(g) && (g!.hard || !g!.armed);
  return (
    <div className="set-foot cmp-foot">
      <input
        ref={file}
        type="file"
        id="cmpFile"
        multiple
        hidden
        onChange={(e) => {
          composing.addFiles([...(e.target.files ?? [])]);
          e.target.value = "";
        }}
      />
      <button type="button" className="abtn" id="cmpAttachBtn" title="Attach files" onClick={() => file.current?.click()}><Icon name="clip" /> Attach</button>
      {offered && (
        <span className="ai-split" id="cmpAiSplit">
          <button type="button" className="abtn ai" id="cmpAiDraft" disabled={c.ai === "draft"} title={`Draft a reply with AI, ${DRAFT_TONES[tone].toLowerCase()} tone`} onClick={() => void composing.aiDraft()}>
            <Icon name="wind" /><span className="ai-t">{c.ai === "draft" ? "Drafting…" : tone === "balanced" ? "Draft" : `Draft · ${DRAFT_TONES[tone].toLowerCase()}`}</span>
          </button>
          <button ref={moreBtn} type="button" className="abtn ai ai-more" id="cmpAiMore" disabled={c.ai === "draft"} aria-label="Draft options" aria-expanded={drafting} onClick={() => setDrafting(!drafting)}>▾</button>
          {drafting && <DraftMenu anchor={moreBtn} onClose={() => setDrafting(false)} />}
        </span>
      )}
      <span className="cmp-saved" id="cmpSaved" aria-live="polite">{c.savedLine}</span>
      <button type="button" className="abtn" id="cmpDiscard" style={{ marginLeft: "auto" }} onClick={() => void composing.discard()}>Discard</button>
      <button type="button" className="abtn" id="cmpSaveDraft" title="Save draft (⌘/Ctrl+S)" onClick={() => void composing.save()}>Save draft</button>
      <button ref={laterBtn} type="button" className="abtn" id="cmpSchedule" title="Send later" onClick={() => composing.laterOpen.set(!composing.laterOpen.get())}><Icon name="clock" /> Later ▾</button>
      <LaterMenu c={c} anchor={laterBtn} />
      <button
        type="button"
        className={holding ? "abtn prim cg-arming" : "abtn prim"}
        id="cmpSend"
        title={sendTitle}
        aria-disabled={holding || undefined}
        aria-keyshortcuts="Meta+Enter Control+Enter"
        onClick={(e) => (g ? composing.confirmGuard(e.detail > 1) : composing.send(c.pendingAfter))}
      >{send}</button>
    </div>
  );
}

function ComposerBox({ c, senders, aliases, ai }: { c: Composer; senders: string[]; aliases: Record<string, string | null>; ai: boolean }) {
  // The first keys land here, not on the list's shortcuts: a new message starts at To, anything else in the text.
  const placeCursor = useEffectEvent(() => {
    if (c.mode === "new" && !c.draftId && !c.to.length) document.querySelector<HTMLInputElement>("#cmpTo .ci")?.focus();
    else {
      const box = document.querySelector<HTMLTextAreaElement>("#cmpBody");
      box?.focus();
      if (!c.note) box?.setSelectionRange(0, 0);
    }
  });
  useEffect(() => placeCursor(), [c.id]);   // once, as this composer opens: an edit must not move the cursor
  const guardShownAt = c.guard?.shownAt;
  useEffect(() => {
    if (guardShownAt !== undefined) document.querySelector<HTMLElement>("#cmpGuard")?.focus();
  }, [guardShownAt]);
  return (
    <div className="set-box cmp-box" id="composeModal">
      <div className="set-head">
        <div>
          <h3 id="cmpTitle">{TITLES[c.mode]}</h3>
          <div className="set-sub" id="cmpSub">Queued to the outbox · sent in the background</div>
        </div>
        <button type="button" className="x" id="cmpClose" aria-label="Close" onClick={() => composing.close()}>✕</button>
      </div>
      <div className="set-body cmp-body">
        {c.note && <p className="cmp-note" id="cmpNote">{c.note}</p>}
        <div className="cmp-row">
          <label htmlFor="cmpFrom">From</label>
          <select id="cmpFrom" className="cmp-from" value={c.from} onChange={(e) => composing.setFrom(e.target.value)}>
            {senders.map((a) => <option key={a} value={a}>{aliases[a] || shortAddress(a)} — {a}</option>)}
          </select>
        </div>
        <div className="cmp-row">
          {/* eslint-disable-next-line jsx-a11y/label-has-associated-control -- a caption: the field's input is named by its aria-label */}
          <label>To</label>
          <ChipField c={c} field="to" label="To" />
          {!c.showCc && <button type="button" className="cmp-cclink" id="cmpCcToggle" onClick={() => { composing.showCopies(); setTimeout(() => document.querySelector<HTMLInputElement>("#cmpCc .ci")?.focus()); }}>Cc/Bcc</button>}
        </div>
        {c.showCc && (
          <>
            {/* eslint-disable-next-line jsx-a11y/label-has-associated-control -- as To's */}
            <div className="cmp-row cmp-optrow" id="cmpCcRow"><label>Cc</label><ChipField c={c} field="cc" label="Cc" /></div>
            {/* eslint-disable-next-line jsx-a11y/label-has-associated-control -- as To's */}
            <div className="cmp-row cmp-optrow" id="cmpBccRow"><label>Bcc</label><ChipField c={c} field="bcc" label="Bcc" /></div>
          </>
        )}
        <div className="cmp-row">
          <label htmlFor="cmpSubject">Subject</label>
          <input id="cmpSubject" className="cmp-subj" placeholder="Subject" value={c.subject} onChange={(e) => composing.edit("subject", e.target.value)} />
        </div>
        {ai && (
          <div className="cmp-airow" id="cmpAiRow" role="group" aria-label="AI rewrite (acts on the selection, or the whole message)">
            <span className="air-label"><Icon name="wind" /><span className="air-t">{c.ai === "rewrite" ? "Rewriting…" : "Rewrite"}</span></span>
            {REWRITES.map(([op, label]) => (
              <button key={op} type="button" className="airw" disabled={c.ai === "rewrite"} onClick={() => void composing.aiRewrite(op)}>{label}</button>
            ))}
            {c.rwUndo && <button type="button" className="airw air-undo" id="cmpRwUndo" onClick={composing.rewriteUndo}>↩ Undo</button>}
          </div>
        )}
        <textarea id="cmpBody" className="cmp-textarea" placeholder="Write your message…" aria-label="message body" value={c.body} onChange={(e) => composing.edit("body", e.target.value)} onPaste={pasteClean} />
        <Quote key={c.id} c={c} />
        <div className="cmp-attach" id="cmpAttach" aria-live="polite">
          {c.attachments.map((a, i) => (
            <span key={`${i}:${a.filename}`} className="cmp-att">
              <Icon name="clip" /> {a.filename} <span className="sz">{fileSize(a.size)}</span>
              <button type="button" aria-label={`Remove ${a.filename}`} onClick={() => composing.removeAttachment(i)}>×</button>
            </span>
          ))}
        </div>
      </div>
      <Guard c={c} />
      <Foot c={c} ai={ai} />
    </div>
  );
}

export function ComposerModal({ senders, aliases, ai }: { senders: string[]; aliases: Record<string, string | null>; ai: boolean }) {
  const c = composing.view.useValue();
  useLayer(Boolean(c), () => {
    if (!composing.view.get()?.guard) return composing.close();
    composing.dismissGuard();
    return false;
  });
  const isOpen = Boolean(c);
  useEffect(() => {
    if (!isOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if ((e.target as Element | null)?.closest?.(".ai-limit")) return;   // the question over the composer answers its own keys
      const held = composing.view.get();
      if (!held) return;
      const chord = e.metaKey || e.ctrlKey;
      if (held.guard && e.key === "Escape") {
        e.preventDefault();
        composing.dismissGuard();
      } else if (chord && e.key === "Enter") {
        e.preventDefault();
        if (held.guard) composing.confirmGuard(e.repeat);
        else composing.send(held.pendingAfter);
      } else if (chord && e.key.toLowerCase() === "s") {
        e.preventDefault();
        void composing.save();
      } else if (e.key === "Escape") {
        e.preventDefault();
        composing.close();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [isOpen]);
  if (!c) return null;
  return (
    <ModalOverlay className="settings show" isOpen isKeyboardDismissDisabled>
      <Modal>
        <Dialog aria-labelledby="cmpTitle">
          <ComposerBox c={c} senders={senders} aliases={aliases} ai={ai} />
        </Dialog>
      </Modal>
    </ModalOverlay>
  );
}

// What a send is doing once the composer has gone: the ten seconds with Undo, the time it went out for, or what failed.
export function SendFloatView() {
  const f = composing.float.useValue();
  if (!f) return null;
  const tone = f.kind === "done" ? "sent" : f.kind === "failed" ? "failed" : "sending";
  const words = f.kind === "countdown" ? <>↑ Sending in {f.left}s…</>
    : f.kind === "failed" ? <><Icon name="alert" /> {f.text}</>
    : <>{f.clock && <Icon name="clock" />} {f.text}</>;
  return (
    <Float id="sendFloat" className={`send-float ${tone}`} shown={{ words, turn: f.kind === "countdown" ? f.id : 0, ...(f.kind === "countdown" ? { life: f.total * 1000 } : {}) }}>
      {f.kind === "countdown" && <button id="sfUndo" onClick={f.undo}>Undo</button>}
      {f.kind === "failed" && <><button id="sfEdit" onClick={f.edit}>Edit</button><button id="sfDismiss" onClick={f.dismiss}>Dismiss</button></>}
    </Float>
  );
}

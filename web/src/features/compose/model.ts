// What a composer is, with no DOM and no server: addresses, the seeds a reply or forward opens with, the requests it makes
// and the send-time check's input. The machine beside it runs these.
import { guardBlocks, guardCheck, guardHard, guardLine, type GuardFix, type GuardSnapshot, type GuardWarning } from "./sendGuard";
import { resumeFields, type ResumeFields } from "./draftResume";

export type Field = "to" | "cc" | "bcc";
export const FIELDS: Field[] = ["to", "cc", "bcc"];
export type Mode = "new" | "reply" | "replyall" | "forward";

export interface Recipient { v: string; bad: boolean }
export interface Attachment { filename: string; content_type: string; size: number; b64: string }
export interface Quote { html: string | null; text: string; from?: string; date?: string }
export interface Ack { subject: boolean; attach: boolean; fanout: number; unchecked: boolean }
export interface Person { known: boolean; self: boolean }

export interface GuardLine { key: string; soft: boolean; text: string; fix: GuardFix | null; field?: Field }
export interface GuardView {
  keys: string[];        // what must be waved through, soft lines aside
  fanN: number;          // how many people the fan-out line counted
  sendAfter: number;
  hard: boolean;         // nothing to wave through: there is no Send anyway
  shownAt: number;
  armed: boolean;
  lines: GuardLine[];
  said: string;          // what the screen reader hears
}

export interface OpenOptions {
  mode?: Mode; from?: string | undefined; to?: string[]; cc?: string[]; bcc?: string[];
  subject?: string; subject0?: string; origPk?: number | null; quote?: Quote | null; draftId?: number | null | undefined; version?: number | null;
  resume?: ResumeFields | null; fwdAttach?: number; noSig?: boolean; autoCc?: string[] | null;
  participants?: string[]; ack?: Partial<Ack> | null; body?: string; attachments?: Attachment[]; note?: string;
}

// The one composer. The machine holds it and changes it, and what it publishes for React is a copy.
export interface Composer {
  id: number;
  mode: Mode;
  from: string;
  to: Recipient[]; cc: Recipient[]; bcc: Recipient[];
  typed: Record<Field, string>;   // what is in a chip field and not yet a chip
  showCc: boolean;
  subject: string; subject0: string;
  body: string;
  sigBlock: string;
  origPk: number | null;
  quote: Quote | null;
  draftId: number | null;
  version: number | null;   // the save this composer last saw, so one it never saw isn't overwritten
  conflict: boolean;        // another window saved since: autosave stops, and only a save someone asks for overwrites
  resume: ResumeFields | null;   // its own threading and quote: a reopened draft, or a reply after its first save
  fwdAttach: number;
  attachments: Attachment[];
  reading: Map<string, string>;   // files still being read: id → name
  idk: string;
  dirty: boolean;
  edits: number;                  // every edit counts: a save clears `dirty` only if none landed while it was in flight
  saving: Promise<boolean | null> | null;
  lastSaveHash: number | null;
  savedLine: string;
  note: string;
  autoCc: string[] | null;        // a reply-all's copied-in people
  ack: Ack;
  guard: GuardView | null;
  pendingAfter: number;           // a Later time the check held back: the next Send schedules for it
  participants: Set<string>;      // the thread's people: never "new"
  people: Map<string, Person>;
  thin: boolean;
  knownQ: Set<string>;
  newSay: Set<string>;
  rwUndo: { value: string; sel: [number, number] } | null;
  ai: "" | "draft" | "rewrite";
}

export function parseAddrs(s: unknown): string[] { return String(s ?? "").match(/[^\s<>,;"]+@[^\s<>,;"]+/g) ?? []; }
export function addrOf(s: unknown): string { return parseAddrs(s)[0] ?? ""; }
// Where a chip's mail goes: the <…> part, not an address in its name.
export function rcptAddr(v: unknown): string {
  const m = String(v ?? "").match(/<([^<>]+)>\s*$/);
  return ((m ? m[1] : addrOf(v)) || String(v ?? "")).trim().toLowerCase();
}
export function rePrefix(subject: string | null | undefined, pre: string): string {
  const s = (subject ?? "").trim();
  return /^(re|fwd?|fw)\s*:/i.test(s) ? `${pre} ${s.replace(/^(re|fwd?|fw)\s*:\s*/i, "")}` : s ? `${pre} ${s}` : pre;
}
export function looksLikeAddr(v: string): boolean {
  const m = v.match(/<([^>]+)>/);
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test((m ? m[1]! : v).trim());
}
export const splitChips = (raw: string) => raw.split(/[,;]/).map((s) => s.trim()).filter(Boolean);
export const chip = (v: string): Recipient => ({ v, bad: !looksLikeAddr(v) });

// What a reply needs of the message it answers.
export interface OriginalMail {
  account: string; subject?: string | null; text?: string | null; html?: string | null; has_html?: boolean;
  from_addr?: string | null; from_name?: string | null; reply_to?: string | null; to?: string | null; cc?: string | null; date?: string | null;
  attachments?: { is_inline?: boolean; content_id?: string | null }[];
}

const quoteOf = (d: OriginalMail): Quote => ({ html: d.has_html ? d.html ?? null : null, text: d.text ?? "", from: d.from_name || d.from_addr || "", date: d.date ?? "" });

export function replySeed(d: OriginalMail, all: boolean, mine: Set<string>): OpenOptions {
  const toAddr = (d.reply_to && addrOf(d.reply_to)) || d.from_addr || "";
  const to = toAddr ? [toAddr] : [];
  const cc: string[] = [];
  if (all) {
    const seen = new Set([toAddr.toLowerCase(), ...mine]);
    for (const a of [...parseAddrs(d.to), ...parseAddrs(d.cc)]) {
      const k = a.toLowerCase();
      if (!seen.has(k)) {
        seen.add(k);
        cc.push(a);
      }
    }
  }
  return {
    mode: all ? "replyall" : "reply", from: d.account, to, cc, autoCc: cc.slice(),
    participants: [d.from_addr ?? "", ...parseAddrs(d.to), ...parseAddrs(d.cc)],   // not Reply-To: a redirect to a stranger gets marked
    subject: rePrefix(d.subject, "Re:"), quote: quoteOf(d),
  };
}

export function forwardSeed(d: OriginalMail): OpenOptions {
  return {
    mode: "forward", from: d.account, to: [], subject: rePrefix(d.subject, "Fwd:"), quote: quoteOf(d),
    fwdAttach: (d.attachments ?? []).filter((a) => !(a.is_inline && a.content_id)).length,
  };
}

export function quoteLabel(c: Pick<Composer, "mode" | "fwdAttach">, open: boolean): string {
  return (open ? "▾ " : "▸ ") + (c.mode === "forward" ? "forwarded message" : "quoted message")
    + (c.fwdAttach ? ` · ${c.fwdAttach} attachment${c.fwdAttach > 1 ? "s" : ""}` : "");
}

const fileParts = (c: Composer) => c.attachments.map((a) => ({ filename: a.filename, content_type: a.content_type, b64: a.b64 }));

export interface Payload {
  account: string; mode: Mode; idempotency_key: string; draft_id: number | null;
  to: string[]; cc: string[]; bcc: string[]; subject: string; text: string; in_reply_to_pk: number | null;
  attachments: { filename: string; content_type: string; b64: string }[];
  resume?: ResumeFields; send_after?: number; autosave?: boolean; base_version?: number;
}

export function payloadOf(c: Composer): Payload {
  return {
    account: c.from, mode: c.mode, idempotency_key: c.idk, draft_id: c.draftId,
    to: c.to.map((x) => x.v), cc: c.cc.map((x) => x.v), bcc: c.bcc.map((x) => x.v),
    subject: c.subject, text: c.body, in_reply_to_pk: c.origPk, attachments: fileParts(c),
    ...resumeFields(c.resume),
  };
}

// A payload built before the save it waited for: that save may have switched the composer over to naming itself.
export function namesItself(p: Payload, c: Composer) {
  if (!c.resume || p.resume) return;
  Object.assign(p, resumeFields(c.resume));
  p.attachments = fileParts(c);
}

// Never litter Drafts with an empty or signature-only message.
export function nothingToSave(c: Composer): boolean {
  const written = (c.sigBlock ? c.body.replace(c.sigBlock, "") : c.body).trim();
  return !c.to.length && !c.cc.length && !c.bcc.length && !c.subject.trim() && !written;
}

function cheapHash(s: string) {
  let h = 0;
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) | 0;
  return h;
}
export const draftHash = (p: Payload) => cheapHash(JSON.stringify([p.to, p.cc, p.bcc, p.subject, p.text, p.account, p.attachments.map((a) => a.filename)]));

export function isNewToYou(c: Composer, a: string, mine: Set<string>): boolean {
  const p = c.people.get(a);
  return Boolean(p) && !p!.known && !p!.self && !c.thin && !mine.has(a) && !c.participants.has(a);
}

export function guardSnapshot(c: Composer, sendAfter: number, nowSeconds: number, mine: Set<string>): GuardSnapshot {
  const n = (x: Recipient) => rcptAddr(x.v);
  return {
    mode: c.mode, subject: c.subject, subject0: c.subject0, body: c.body, sigBlock: c.sigBlock,
    nAttach: c.attachments.length, fwdAttach: c.fwdAttach, reading: [...c.reading.values()], sendAfter, now: nowSeconds,
    to: c.to.map(n), cc: c.cc.map(n), mine: [...mine, ...[...c.people].filter(([, p]) => p.self).map(([a]) => a)],
    autoCc: c.autoCc, ack: c.ack,
    fresh: [...new Set([...c.to, ...c.cc, ...c.bcc].filter((x) => !x.bad).map(n))].filter((a) => isNewToYou(c, a, mine)),
  };
}

export type AnyWarning = GuardWarning | { key: "unchecked"; block: "confirm" } | { key: "norcpt"; block: "hard" } | { key: "bad"; block: "hard"; v: string; more: number; field: Field };

// What stops a send before the checks proper: nobody to send it to, or a chip that isn't an address.
export function recipientProblem(c: Composer): AnyWarning | null {
  if (!c.to.length && !c.cc.length && !c.bcc.length) return { key: "norcpt", block: "hard" };
  const bad = FIELDS.flatMap((f) => c[f].filter((x) => x.bad).map((x) => ({ f, v: x.v })));
  return bad.length ? { key: "bad", block: "hard", v: bad[0]!.v, more: bad.length - 1, field: bad[0]!.f } : null;
}

// The checks, which a bug in them turns into one line to wave through and never into a silent send.
export function runChecks(c: Composer, sendAfter: number, nowSeconds: number, mine: Set<string>): AnyWarning[] {
  try {
    return guardCheck(guardSnapshot(c, sendAfter, nowSeconds, mine));
  } catch (err) {
    console.error("[guard]", err);
    return c.ack.unchecked ? [] : [{ key: "unchecked", block: "confirm" }];
  }
}
export const blocks = (w: AnyWarning[]) => guardBlocks(w as GuardWarning[]);
export const isHard = (w: AnyWarning[]) => guardHard(w as GuardWarning[]);

export function guardView(warnings: AnyWarning[], sendAfter: number, shownAt: number, fmt: (epoch: number) => string): GuardView {
  const hard = isHard(warnings), later = sendAfter > 0;
  const lines = warnings.map((w): GuardLine => {
    const line = w.key === "late" ? { ...w, whenText: fmt(w.when) } : w;
    const { text, fix } = guardLine(line as Parameters<typeof guardLine>[0]);
    return { key: w.key, soft: w.block === "soft", text, fix, ...(w.key === "bad" ? { field: w.field } : {}) };
  });
  const spoken = hard ? "Press Escape to keep editing." : `Press Command or Control Enter to ${later ? "schedule" : "send"} anyway, or Escape to keep editing.`;
  return {
    keys: warnings.filter((x) => x.block !== "soft").map((x) => x.key),
    fanN: (warnings.find((x) => x.key === "fanout") as { n?: number } | undefined)?.n ?? 0,
    sendAfter, hard, shownAt, armed: false, lines,
    said: `${lines.map((l) => l.text).join(" ")} ${spoken}`,
  };
}

// Friendly local time for a scheduled send.
export function fmtWhen(epoch: number, now = new Date()): string {
  const d = new Date(epoch * 1000);
  const t = d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  if (d.toDateString() === now.toDateString()) return `today ${t}`;
  const tomorrow = new Date(now);
  tomorrow.setDate(now.getDate() + 1);
  if (d.toDateString() === tomorrow.toDateString()) return `tomorrow ${t}`;
  return `${d.toLocaleDateString([], { weekday: "short", month: "short", day: "numeric" })} ${t}`;
}

export function sendLaterPresets(now = new Date()): [string, number][] {
  const sec = (d: Date) => Math.floor(d.getTime() / 1000);
  const evening = new Date(now);
  evening.setHours(18, 0, 0, 0);
  if (evening <= now) evening.setDate(evening.getDate() + 1);
  const tomorrow = new Date(now);
  tomorrow.setDate(now.getDate() + 1);
  tomorrow.setHours(8, 0, 0, 0);
  const monday = new Date(now);
  monday.setDate(now.getDate() + (((8 - now.getDay()) % 7) || 7));
  monday.setHours(8, 0, 0, 0);
  return [["In 1 hour", sec(new Date(now.getTime() + 3600e3))], ["This evening", sec(evening)], ["Tomorrow 8 AM", sec(tomorrow)], ["Monday 8 AM", sec(monday)]];
}

export const QUICK: { key: string; chip: string; line: string; ask: string }[] = [
  { key: "yes", chip: "Yes, that works", line: "Yes, that works for me.", ask: "Say yes: agree to or confirm what they asked." },
  { key: "no", chip: "Can’t, sorry", line: "Sorry, I can’t make that work.", ask: "Politely say no to what they asked, without inventing a reason." },
  { key: "thanks", chip: "Thanks!", line: "Thanks!", ask: "Just thank them, briefly and warmly." },
  { key: "time", chip: "Ask for a time", line: "Could you send me a few times that work for you?", ask: "Ask them to suggest a few times that suit them; don’t propose any yourself." },
];

// A person you could answer: not you, not a no-reply robot, not junk or a receipt.
export function canQuickReply(addr: string | null | undefined, mine: Set<string>, row?: { isJunk?: boolean; bucket?: string | null }): boolean {
  const a = (addr ?? "").toLowerCase();
  if (!a || mine.has(a) || /^(no-?reply|do-?not-?reply|mailer-daemon|postmaster|notifications?|bounces?)\b/.test(a)) return false;
  return !(row?.isJunk || /^(receipts|automated|newsletters|spam|blocked)/.test(row?.bucket ?? ""));
}

export const DRAFT_TONES: Record<string, string> = { brief: "Brief", balanced: "Balanced", warm: "Warm", formal: "Formal" };

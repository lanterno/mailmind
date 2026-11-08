// The composer's rules, apart from any screen. One composer at a time; its saves go in turn, each naming the copy the last
// one left; a save clears `dirty` only if no edit landed while it was in flight; the send-time check runs synchronously, so
// nothing is awaited before the composer closes and a double submit can't happen; and what is closed with unsaved words
// keeps them a click away. CLAUDE.md's "Writes, undo, and offline" says why each of these is so.
import { GUARD_ARM_MS, guardFanout, guardLine, guardList, isMine, type GuardSwap, type NearMiss } from "./sendGuard";
import { resumeHidesAi, resumeNotice, resumeOpts, resumeSig, type ReopenedDraft, type ResumeFields } from "./draftResume";
import type { LIMITS } from "../../lib/api/limits.gen";
import type { DraftTone, RewriteOp } from "../../lib/api/types";
import type { Copy } from "../../lib/copy";
import type { Store } from "../../lib/store";
import {
  blocks, chip, DRAFT_TONES, draftHash, FIELDS, fmtWhen, forwardSeed, guardView, isHard, isNewToYou, looksLikeAddr, namesItself, nothingToSave, payloadOf,
  QUICK, rcptAddr, recipientProblem, replySeed, runChecks, splitChips,
  type AnyWarning, type Attachment, type Composer, type Field, type OpenOptions, type OriginalMail, type Payload, type Person,
} from "./model";

export type Focus = "guard" | "send" | "body" | "subject" | "schedule" | "attach" | { field: Field };

// eslint-disable-next-line @typescript-eslint/no-explicit-any -- the answer of whichever route was asked, read field by field, with machine.test.ts holding what it reads
export type Answer = any;

export interface Env {
  createStore<T>(initial: T): Store<T>;   // how what the machine publishes is held, which is the screen's business
  post(path: string, body: unknown, ai?: boolean): Promise<Answer>;   // ai: a request a person started, which may ask to go over the daily limit
  get(path: string): Promise<Answer>;
  toast(message: string): void;
  say(message: string): void;
  senders(): string[];
  current(): string | undefined;
  mine(): Set<string>;
  signature(email: string): string;
  tone(): DraftTone;
  limits: Limits;   // the server's, as lib/api/limits.gen.ts has them
  // Where focus is, to put it back after a check; and to put it somewhere the machine names.
  focusHere(): unknown;
  focusBack(where: unknown, fallback: Focus): void;
  focus(what: Focus): void;
  pickFile(): void;
  readFile(file: File): Promise<string>;
  prompt(question: string, fallback: string): string | null;
  selection(): [number, number];
  select(start: number, end: number): void;
  // What else has to know.
  draftsChanged(): void;
  foldersChanged(): void;
  outboxChanged(): void;
  sent(replyKey: number | null): void;   // the key of the message a reply answered
  draftGone(copy: Copy): void;
  now(): number;     // epoch seconds
  clock(): number;   // ms, for the arm delay
}

export type Limits = { readonly [K in keyof typeof LIMITS]: number };

export type SendFloat =
  | { kind: "progress"; text: string; clock?: boolean }
  | { kind: "countdown"; left: number; undo: () => void }
  | { kind: "done"; text: string; clock?: boolean }
  | { kind: "failed"; text: string; edit: () => void; dismiss: () => void };

type Snapshot = Pick<Composer, "mode" | "from" | "to" | "cc" | "bcc" | "subject" | "body" | "origKey" | "quote" | "fwdAttach" | "attachments" | "draftId" | "resume" | "autoCc" | "subject0"> & { participants: string[]; ack: Pick<Composer["ack"], "subject" | "attach" | "fanout" | "near"> };

const AUTOSAVE_MS = 8000;
const KNOWN_WAIT_MS = 150;

// An Undo Send the server said no to: it's past its window (a 409), and goes as it was going.
const tooLate = (e: unknown) => (e as { status?: unknown } | null)?.status === 409;

const randomId = () => (typeof crypto !== "undefined" && crypto.randomUUID ? crypto.randomUUID() : `id-${Date.now()}-${Math.random()}`);

export function createComposing(env: Env) {
  const view = env.createStore<Composer | null>(null);
  const float = env.createStore<SendFloat | null>(null);
  const laterOpen = env.createStore(false);
  let c: Composer | null = null;
  let nextId = 1;
  let autosaver: ReturnType<typeof setInterval> | undefined;
  let knownTimer: ReturnType<typeof setTimeout> | undefined;
  let armTimer: ReturnType<typeof setTimeout> | undefined;
  let undoTimer: ReturnType<typeof setInterval> | undefined;
  let floatTimer: ReturnType<typeof setTimeout> | undefined;
  let closeSaves: Promise<unknown> | null = null;   // the saves close started, until all settle: Edit waits for them
  let failedSend: (Snapshot & { draftId: number | null }) | null = null;
  let roomBusy = false, resumeTurn = 0, openingAhead: number | null = null;
  const knownCache = new Map<string, Person>();     // page lifetime, known/self answers only
  let guardBack: unknown = null;

  const stillOpen = (x: Composer) => c === x;
  const publish = (x: Composer | null = c) => {
    if (x === c) view.set(c ? { ...c } : null);
  };
  const markEdited = (x: Composer) => {
    x.dirty = true;
    x.edits += 1;
  };

  function showFloat(next: SendFloat | null, forMs = 0) {
    clearTimeout(floatTimer);
    float.set(next);
    if (next && forMs) floatTimer = setTimeout(() => float.set(null), forMs);
  }

  /* ---------- opening and closing ---------- */
  function open(opts: OpenOptions = {}): Composer | null {
    const senders = env.senders();
    if (!senders.length) {
      env.toast("No sending account configured — add one in Settings");
      return null;
    }
    const current = env.current();
    const from = opts.from && senders.includes(opts.from) ? opts.from : current && senders.includes(current) ? current : senders[0]!;
    stopTimers();
    let body = opts.body ?? "", sigBlock = "";
    if (opts.body !== undefined) sigBlock = resumeSig(opts.body, env.signature(opts.from ?? from));
    else if (!opts.draftId && !opts.noSig) {
      const sig = env.signature(from);
      if (sig) body = sigBlock = `\n\n-- \n${sig}`;   // write above the signature, not after it
    }
    const to = (opts.to ?? []).map((v) => ({ v, bad: false })), cc = (opts.cc ?? []).map((v) => ({ v, bad: false })), bcc = (opts.bcc ?? []).map((v) => ({ v, bad: false }));
    c = {
      id: nextId++, mode: opts.mode ?? "new", from, to, cc, bcc, typed: { to: "", cc: "", bcc: "" }, showCc: cc.length > 0 || bcc.length > 0,
      subject: opts.subject ?? "", subject0: opts.subject0 ?? opts.subject ?? "", body, sigBlock,
      origKey: opts.origKey ?? null, quote: opts.quote ?? null, draftId: opts.draftId ?? null, version: opts.version ?? null, conflict: false, resume: opts.resume ?? null,
      fwdAttach: opts.fwdAttach ?? 0, attachments: opts.attachments ?? [], reading: new Map(), idk: randomId(),
      dirty: false, edits: 0, saving: null, lastSaveHash: null, savedLine: "", note: opts.note ?? "",
      autoCc: opts.autoCc ? opts.autoCc.map((a) => a.toLowerCase()) : null,
      ack: { subject: Boolean(opts.ack?.subject), attach: Boolean(opts.ack?.attach), fanout: Number(opts.ack?.fanout) || 0, unchecked: false, near: opts.ack?.near ?? [] },
      guard: null, pendingAfter: 0, participants: new Set((opts.participants ?? []).map((a) => a.toLowerCase())),
      people: new Map(), thin: false, knownQ: new Set(), newSay: new Set(), rwUndo: null, ai: "",
    };
    autosaver = setInterval(autosaveTick, AUTOSAVE_MS);
    knownQueue(c, [...c.to, ...c.cc, ...c.bcc].map((x) => x.v), false);   // prefilled chips are marked silently
    publish();
    return c;
  }

  function stopTimers() {
    clearInterval(autosaver);
    clearTimeout(knownTimer);
    clearTimeout(armTimer);
  }

  function snapshot(x: Composer): Snapshot {
    return {
      mode: x.mode, from: x.from, to: x.to, cc: x.cc, bcc: x.bcc, subject: x.subject, body: x.body, origKey: x.origKey, quote: x.quote, fwdAttach: x.fwdAttach,
      attachments: x.attachments, draftId: x.draftId, resume: x.resume, autoCc: x.autoCc, subject0: x.subject0, participants: [...x.participants],
      ack: { subject: x.ack.subject, attach: x.ack.attach, fanout: x.ack.fanout, near: x.ack.near },
    };
  }

  // A closed composer's words: failed to save, or an Undo Send. Nothing holds them but this.
  function reopen(s: Snapshot) {
    const opened = open({
      mode: s.mode, from: s.from, to: s.to.map((x) => x.v), cc: s.cc.map((x) => x.v), bcc: s.bcc.map((x) => x.v), subject: s.subject, subject0: s.subject0,
      origKey: s.origKey, quote: s.quote, fwdAttach: s.fwdAttach, draftId: s.draftId, resume: s.resume, noSig: true, autoCc: s.autoCc, participants: s.participants,
      ack: s.ack, body: s.body, attachments: s.attachments,
    });
    if (opened) {
      for (const f of FIELDS) opened[f] = s[f];
      markEdited(opened);   // closing it saves
      publish();
    }
  }

  function closeSaveFailed(x: Composer, s: Snapshot) {
    failedSend = { ...s, draftId: x.draftId };
    const m = "Your message couldn’t be saved";
    showFailed(m);
    env.say(m);
  }

  function close(force = false) {
    const x = c;
    if (!x) return;
    stopTimers();
    laterOpen.set(false);
    if (!force) commitTyped(x);
    const closingSave = x.dirty && !force;
    if (closingSave) {
      const saved = save(true), s = snapshot(x);   // the save read the fields: the snapshot holds them
      const settled = Promise.all([closeSaves, saved.then((ok) => {
        if (ok) {
          env.toast("Saved to Drafts");
          env.draftsChanged();
        } else if (ok === false) closeSaveFailed(x, s);
      })]);
      closeSaves = settled;
      void settled.finally(() => {
        if (closeSaves === settled) closeSaves = null;
      });
    }
    c = null;
    view.set(null);
    if (x.draftId && !closingSave) env.draftsChanged();   // its saves replaced the copy that list shows
  }

  async function discard() {   // throw it away, the copy on the server too
    const x = c;
    if (!x) return;
    if (x.saving) await x.saving;   // a first save still in flight would leave its copy behind
    if (x.draftId) {
      try {
        await env.post("/api/draft", { draft_id: x.draftId, delete: true });
      } catch { /* the copy stays; it's still a draft */ }
      env.foldersChanged();
    }
    x.dirty = false;
    if (c === x) close(true);
  }

  // Replace the open composer once its save lands, or say it stays. open() runs in the same task as the close.
  async function makeRoom(then: () => void): Promise<boolean> {
    const x = c;
    if (x && roomBusy) return false;
    if (x && x.dirty) {
      roomBusy = true;
      let ok: boolean | null;
      try {
        do ok = await save(true); while (ok && c === x && x.dirty);   // typed during the save: that too
      } finally {
        roomBusy = false;
      }
      if (c && c !== x) return false;   // another opened meanwhile (one closed meanwhile saved itself)
      if (ok === false && c) {
        const m = "The message you’re writing isn’t saved, so it stays open";
        env.toast(m);
        env.say(m);
        return false;
      }
    }
    if (c) close(true);   // saved, or nothing worth saving
    then();
    return true;
  }

  /* ---------- editing ---------- */
  function retireGuard(x: Composer) {   // any real edit retires the check; Send checks again from scratch
    const g = x.guard;
    if (!g) return;
    if (g.keys.includes("reading") && x.reading.size) {   // still reading: the line stays, naming what's left
      const left = [...x.reading.values()];
      const { text } = guardLine({ key: "reading", name: left[0]!, more: left.length - 1 });
      x.guard = { ...g, lines: g.lines.map((l) => (l.key === "reading" ? { ...l, text } : l)), said: `${text} Press Escape to keep editing.` };
      return;
    }
    hideGuard(x);
  }
  function hideGuard(x: Composer) {
    clearTimeout(armTimer);
    x.guard = null;
  }
  function edit(field: "subject" | "body", value: string) {
    if (!c) return;
    c[field] = value;
    markEdited(c);
    retireGuard(c);
    publish();
  }
  function setFrom(value: string) {
    if (!c) return;
    c.from = value;
    const sig = env.signature(value), block = sig ? `\n\n-- \n${sig}` : "";
    let caret: [number, number] | null = null;
    if (c.sigBlock ? c.body.endsWith(c.sigBlock) : block) {   // a signature you edited is yours, and stays
      const keep = c.body.length - c.sigBlock.length, [start, end] = env.selection();
      c.body = c.body.slice(0, keep) + block;
      caret = [Math.min(start, keep), Math.min(end, keep)];   // the caret stays in what you wrote
    }
    c.sigBlock = block;
    markEdited(c);
    retireGuard(c);
    publish();
    if (caret) env.select(caret[0], caret[1]);
  }
  function setTyped(field: Field, value: string) {
    if (!c) return;
    c.typed[field] = value;
    markEdited(c);
    retireGuard(c);
    publish();
  }
  function commitChip(field: Field, raw: string) {
    if (!c) return;
    const added = splitChips(raw);
    for (const v of added) {
      c[field] = [...c[field], chip(v)];
      markEdited(c);
    }
    c.typed[field] = "";
    knownQueue(c, added, true);
    knownQueue(c, [...c.to, ...c.cc, ...c.bcc].map((x) => x.v), false);   // a lookup that failed is asked again
    retireGuard(c);
    publish();
  }
  function commitTyped(x: Composer) {
    for (const f of FIELDS) if (x.typed[f].trim()) commitChip(f, x.typed[f]);
  }
  function removeChip(field: Field, i: number) {
    if (!c) return;
    c[field] = c[field].filter((_, n) => n !== i);
    markEdited(c);
    retireGuard(c);
    publish();
    env.focus({ field });
  }
  const showCopies = () => {
    if (!c) return;
    c.showCc = true;
    publish();
  };

  /* ---------- "new to you": a soft mark, never a block ---------- */
  function knownQueue(x: Composer, values: string[], say: boolean) {
    const mine = env.mine();
    for (const v of values) {
      const a = rcptAddr(v);
      if (!a || !looksLikeAddr(a) || isMine(a, mine) || x.participants.has(a)) continue;
      const cached = knownCache.get(a);
      if (cached) {
        x.people.set(a, cached);
        continue;
      }
      if (x.people.has(a)) continue;
      x.knownQ.add(a);
      if (say) x.newSay.add(a);
    }
    if (x.knownQ.size) {
      clearTimeout(knownTimer);
      knownTimer = setTimeout(() => void knownFlush(x), KNOWN_WAIT_MS);
    }
  }
  async function knownFlush(x: Composer) {   // one request for every queued address, never one per chip
    if (c !== x) return;
    const addrs = [...x.knownQ].slice(0, 100);
    addrs.forEach((a) => x.knownQ.delete(a));
    let d: Answer;
    try {
      d = await env.post("/api/contacts/known", { addrs });
    } catch {
      return;   // pending or failed: no mark at all
    }
    if (c !== x) return;
    x.thin = Boolean(d.thin);
    for (const [a, p] of Object.entries<Answer>(d.items ?? {})) {
      const v: Person = { known: Boolean(p.known), self: Boolean(p.self), ...(p.near ? { near: String(p.near) } : {}) };
      x.people.set(a, v);
      if (v.known || v.self) knownCache.set(a, v);
    }
    const mine = env.mine();
    const hits = [...x.newSay].filter((a) => isNewToYou(x, a, mine));
    x.newSay.clear();
    if (hits.length && !x.guard) env.say(`New to you: ${guardList(hits)}.`);
    publish();
    if (x.knownQ.size) await knownFlush(x);
  }

  /* ---------- files ---------- */
  function addFiles(files: Iterable<File>) {
    const x = c;
    if (!x) return;
    let total = x.attachments.reduce((s, a) => s + a.size, 0);
    for (const f of files) {
      if (total + f.size > env.limits.attach_bytes) {
        env.toast(`Attachments too large (${Math.round(env.limits.attach_bytes / 1048576)} MB max)`);
        continue;
      }
      total += f.size;
      const id = randomId();
      x.reading.set(id, f.name);
      env.readFile(f).then(
        (b64) => {
          x.reading.delete(id);
          x.attachments = [...x.attachments, { filename: f.name, content_type: f.type || "application/octet-stream", size: f.size, b64 }];
          markEdited(x);
          if (c === x) {
            retireGuard(x);
            publish();
          }
        },
        () => {
          x.reading.delete(id);
          if (c === x) {
            env.toast(`Couldn’t add ${f.name}`);
            env.say(`Couldn’t add ${f.name}.`);
            retireGuard(x);
            publish();
          }
        },
      );
    }
    publish();
  }
  function removeAttachment(i: number) {
    if (!c) return;
    c.attachments = c.attachments.filter((_, n) => n !== i);
    markEdited(c);
    retireGuard(c);
    publish();
  }

  /* ---------- saving ---------- */
  // A reply's or forward's first save: from now on it names its quote, threading and files itself, so the original's row can go.
  function adoptResume(x: Composer, r: { resume?: ResumeFields; files?: Attachment[] }) {
    if (!r.resume || x.resume) return;
    x.resume = r.resume;
    if (!x.fwdAttach) return;
    x.attachments = x.attachments.concat(r.files ?? []);   // the server no longer adds them: they are this composer's files now
    x.fwdAttach = 0;
  }

  // → true once saved, false if it wasn't, null when there's nothing to save. Half an address isn't a recipient yet, so
  // autosave leaves what's being typed where it is; a save anyone asked for takes it.
  function save(silent = false, takeTyped = true, autosave = false): Promise<boolean | null> {
    const x = c;
    if (!x) return Promise.resolve(null);
    if (takeTyped) commitTyped(x);
    if (nothingToSave(x)) {
      if (!silent) env.toast("Nothing to save yet");
      return Promise.resolve(null);
    }
    const payload = payloadOf(x), at = x.edits, prior = x.saving;
    const line = (t: string) => {
      x.savedLine = t;
      publish(x);
    };
    const mine = (async () => {
      if (prior) await prior;   // one composer's saves go in turn, so each names the copy the last one left
      payload.draft_id = x.draftId;
      namesItself(payload, x);
      if (autosave) payload.autosave = true;
      if (x.version !== null && (silent || !x.conflict)) payload.base_version = x.version;   // a save someone asked for overwrites
      try {
        const r = await env.post("/api/draft", payload);
        x.draftId = r.draft_id;
        x.version = r.version ?? x.version;
        x.conflict = false;
        adoptResume(x, r);
        if (x.edits === at) x.dirty = false;   // an edit since this save began stays to save
        x.lastSaveHash = draftHash(payload);
        line(`Saved ${new Date().toTimeString().slice(0, 5)}`);
        if (!silent) env.toast("Draft saved");
        if (!autosave && r.pushed === false) env.outboxChanged();   // it waits for its server: the tray says so
        return true;
      } catch (e) {
        if ((e as { status?: number }).status === 409) {
          x.conflict = true;
          line("Changed in another window");
          if (!silent) env.toast("This draft changed in another window. Save again to keep this text.");
          return false;
        }
        line("Not saved");   // never leave an older "Saved" up
        if (!silent) env.toast(`Couldn’t save draft: ${e instanceof Error ? e.message : ""}`);
        return false;
      }
    })();
    x.saving = mine;
    void mine.finally(() => {
      if (x.saving === mine) x.saving = null;
    });
    return mine;
  }

  function autosaveTick() {
    const x = c;
    if (!x || !x.dirty || x.saving || x.conflict || nothingToSave(x)) return;   // the next tick saves what this one would
    if (draftHash(payloadOf(x)) !== x.lastSaveHash) void save(true, false, true);
  }

  /* ---------- sending ---------- */
  function showGuard(x: Composer, warnings: AnyWarning[], sendAfter: number, back: unknown, lead = "") {
    const g = guardView(warnings, sendAfter, env.clock(), fmtWhen);
    x.guard = g;
    guardBack = back;
    clearTimeout(armTimer);
    if (!g.hard) {
      armTimer = setTimeout(() => {
        if (x.guard === g || (x.guard && x.guard.shownAt === g.shownAt)) {
          x.guard = { ...x.guard, armed: true };
          publish(x);
        }
      }, GUARD_ARM_MS);
    }
    publish(x);
    env.focus("guard");
    env.say(`${lead ? `${lead} ` : ""}${g.said}`);
  }

  function dismissGuard() {   // Esc, Keep editing: return to where you were
    const x = c, g = x?.guard;
    if (!x || !g) return;
    hideGuard(x);
    publish(x);
    env.focusBack(guardBack, g.sendAfter > 0 ? "schedule" : "body");
  }

  function confirmGuard(repeat = false) {
    const x = c, g = x?.guard;
    if (!x || !g) return;
    if (g.hard) {   // nothing to wave through: say why again
      if (!repeat) {
        env.focus("guard");
        env.say(g.said);
      }
      return;
    }
    if (repeat || env.clock() - g.shownAt < GUARD_ARM_MS) return;   // a double-click (the strip shifts the layout under it) or held keys never confirm
    const now = runChecks(x, g.sendAfter, env.now(), env.mine());
    const conf = now.filter((w) => w.block === "confirm"), fan = now.find((w) => w.key === "fanout") as { n?: number } | undefined;
    if (isHard(now) || conf.some((w) => !g.keys.includes(w.key)) || (fan?.n ?? 0) > g.fanN) {   // the time passed, or an AI edit changed things
      showGuard(x, now, g.sendAfter, guardBack, "Something changed.");
      return;
    }
    for (const k of g.keys) {
      if (k === "fanout") x.ack.fanout = Math.max(x.ack.fanout, g.fanN);
      else if (k === "near") x.ack.near = [...x.ack.near, ...(now.find((w) => w.key === "near") as { pairs: NearMiss[] } | undefined)?.pairs.map((p) => p.addr) ?? []];
      else if (k === "subject" || k === "attach" || k === "unchecked") x.ack[k] = true;
    }
    const after = g.sendAfter;
    hideGuard(x);
    send(after);
  }

  function trimCc() {   // keep only the Cc you added yourself; it's a plain reply after that
    const x = c;
    if (!x?.autoCc) return;
    const n = (r: { v: string }) => rcptAddr(r.v), before = x.cc.length, auto = x.autoCc;
    x.cc = x.cc.filter((r) => !auto.includes(n(r)));
    const k = before - x.cc.length;
    x.mode = "reply";
    markEdited(x);
    hideGuard(x);
    publish();
    env.focus("send");
    const m = guardFanout(x.to.map(n), x.cc.map(n), [...env.mine()]);
    env.say(`Removed ${k} ${k === 1 ? "person" : "people"}. This reply goes to ${m} ${m === 1 ? "person" : "people"}.`);
  }

  // A line's fix that names the right address: the chip keeps its name and takes that one.
  function swapAddress(x: Composer, { from, to }: GuardSwap) {
    const named = (v: string) => (/<[^<>]*>\s*$/.test(v) ? v.replace(/<[^<>]*>\s*$/, `<${to}>`) : to);
    for (const f of FIELDS) x[f] = x[f].map((r) => (rcptAddr(r.v) === from ? chip(named(r.v)) : r));
    markEdited(x);
    hideGuard(x);
    publish();
    env.focus("send");
    env.say(`Changed to ${to}.`);
  }

  function guardFix(kind: string, field?: Field, swap?: GuardSwap) {
    const x = c;
    if (!x) return;
    if (kind === "trim") return trimCc();
    if (swap) return swapAddress(x, swap);
    hideGuard(x);
    if (kind === "review") x.showCc = true;
    if (kind === "bad" && field) {   // back in the input, to fix it
      const i = x[field].findIndex((r) => r.bad);
      if (i < 0) return publish();
      x.typed[field] = x[field][i]!.v;
      x[field] = x[field].filter((_, n) => n !== i);
      if (field !== "to") x.showCc = true;
    }
    publish();
    if (kind === "subject") env.focus("subject");
    else if (kind === "attach") {
      env.focus("attach");
      env.pickFile();
    } else if (kind === "review") env.focus({ field: "cc" });
    else if (kind === "time") laterOpen.set(true);
    else if (kind === "to") env.focus({ field: "to" });
    else if (kind === "bad" && field) env.focus({ field });
  }

  function send(sendAfter = 0) {
    const x = c;
    if (!x) return;
    const back = env.focusHere();   // before the chip commit below
    x.pendingAfter = sendAfter > 0 ? sendAfter : 0;   // kept if a check holds this attempt
    commitTyped(x);
    const problem = recipientProblem(x);
    if (problem) return showGuard(x, [problem], sendAfter, back);
    const warnings = runChecks(x, sendAfter, env.now(), env.mine());
    if (blocks(warnings)) return showGuard(x, warnings, sendAfter, back);
    hideGuard(x);
    const scheduled = sendAfter > 0;
    const payload: Payload = { ...payloadOf(x), send_after: scheduled ? sendAfter : 0 };
    const replyKey = x.mode === "reply" || x.mode === "replyall" ? x.origKey : null;
    const snap = snapshot(x);   // Undo → resend unchanged isn't asked again
    const saving = x.saving;   // a save still in flight: the send must name the copy it leaves
    x.dirty = false;
    failedSend = null;
    close(true);
    showFloat(scheduled ? { kind: "progress", clock: true, text: `Scheduling for ${fmtWhen(sendAfter)}…` } : { kind: "progress", text: "↑ Sending…" });
    void (async () => {
      try {
        if (saving) {
          await saving;
          payload.draft_id = x.draftId;
          namesItself(payload, x);
          Object.assign(snap, { draftId: x.draftId, resume: x.resume, attachments: x.attachments, fwdAttach: x.fwdAttach });
        }
        const r = await env.post("/api/send", payload);
        if (scheduled) showFloat({ kind: "done", clock: true, text: `Scheduled · ${fmtWhen(sendAfter)}` }, 2600);
        else countdown(r.outbox_id, snap, r.recipients);   // the grace window, with Undo
        env.sent(replyKey);
        env.outboxChanged();
      } catch (e) {
        failedSend = { ...snap, draftId: x.draftId };
        showFailed(`Send failed — ${e instanceof Error ? e.message : ""}`);
      }
    })();
  }

  function showFailed(text: string) {
    showFloat({ kind: "failed", text, edit: () => void reopenFailed(), dismiss: () => showFloat(null) });
  }

  function countdown(id: number, snap: Snapshot, recipients: number | undefined) {
    clearInterval(undoTimer);
    let left = env.limits.undo_send_seconds;
    const undo = () => void undoSend(id, snap);
    showFloat({ kind: "countdown", left, undo });
    undoTimer = setInterval(() => {
      left -= 1;
      if (left <= 0) {
        clearInterval(undoTimer);
        showFloat({ kind: "done", text: `✓ Sent${recipients ? ` · ${recipients} recipient${recipients > 1 ? "s" : ""}` : ""}` }, 2400);
        env.outboxChanged();
      } else showFloat({ kind: "countdown", left, undo });
    }, 1000);
  }

  // Something the server built and queued, with the same window to undo it in: an RSVP.
  function queued(id: number, what: string) {
    clearInterval(undoTimer);
    let left = env.limits.undo_send_seconds;
    const undo = () => {
      clearInterval(undoTimer);
      void (async () => {
        try {
          await env.post("/api/outbox/cancel", { id });
          showFloat(null);
          env.toast(`${what} not sent`);
          env.outboxChanged();
        } catch (e) {
          showFloat(tooLate(e) ? { kind: "done", text: "✓ Sent — too late to undo" } : { kind: "done", text: "✓ Sent" }, tooLate(e) ? 2200 : 2000);
        }
      })();
    };
    showFloat({ kind: "countdown", left, undo });
    undoTimer = setInterval(() => {
      left -= 1;
      if (left <= 0) {
        clearInterval(undoTimer);
        showFloat({ kind: "done", text: `✓ ${what} sent` }, 2400);
        env.outboxChanged();
      } else showFloat({ kind: "countdown", left, undo });
    }, 1000);
    env.outboxChanged();
  }

  async function undoSend(id: number, snap: Snapshot) {
    clearInterval(undoTimer);
    try {
      await env.post("/api/outbox/cancel", { id });
      showFloat(null);
      await reopenSend(snap);
      env.toast("Send undone");
      env.outboxChanged();
    } catch (e) {
      showFloat(tooLate(e) ? { kind: "done", text: "✓ Sent — too late to undo" } : { kind: "done", text: "✓ Sent" }, tooLate(e) ? 2200 : 2000);
    }
  }

  async function reopenSend(snap: Snapshot) {
    const ok = await makeRoom(() => reopen(snap));
    if (!ok) {   // the open message couldn't be saved: keep it, and this one a click away
      failedSend = { ...snap, draftId: snap.draftId };
      showFailed("Send undone — not reopened yet");
    }
  }

  async function reopenFailed() {
    const f = failedSend;
    if (!f) return;
    await makeRoom(() => {   // if the open message can't be saved, the float and its Edit stay for a second try
      if (failedSend === f) showFloat(null);   // a send that failed during the save keeps its float
      reopen(f);
    });
  }

  /* ---------- from the mail ---------- */
  // The copy on screen is read for the quote; the reply names the message by its key, which any copy of it answers to.
  const original = async (copy: Copy): Promise<OriginalMail | null> => {
    try {
      return await env.get(`/api/message/body?key=${copy.key}&folder=${encodeURIComponent(copy.folder)}&uid=${copy.uid}`);
    } catch {
      return null;
    }
  };

  async function reply(to: Copy, all = false): Promise<Composer | null> {
    const d = await original(to);
    if (!d) {
      env.toast("Couldn’t load the message");
      return null;
    }
    return open({ ...replySeed(d, all, env.mine()), origKey: to.key });
  }

  async function forward(of: Copy): Promise<Composer | null> {
    const d = await original(of);
    if (!d) {
      env.toast("Couldn’t load the message");
      return null;
    }
    return open({ ...forwardSeed(d, env.limits.attach_bytes), origKey: of.key });
  }

  // Your text in the box, the quote folded below, files, Bcc and thread as saved.
  async function editDraft(copy: Copy | null, draftId?: number) {   // by its copy, or by its id for one the list can't show yet
    const busy = "Close the message you’re writing first";
    if (c) return env.toast(busy);
    const turn = ++resumeTurn;
    if (closeSaves) {   // the copy it saves replaces the one this row shows
      await closeSaves;
      if (turn !== resumeTurn) return;
    }
    let d: ReopenedDraft & { text?: string; files?: Attachment[]; notes?: Parameters<typeof resumeNotice>[0] };
    try {
      d = await env.post("/api/draft/resume", copy === null ? { draft_id: draftId } : { key: copy.key, folder: copy.folder, uid: copy.uid });
    } catch (e) {
      if (turn !== resumeTurn) return;
      const m = (e instanceof Error && e.message) || "Couldn’t open this draft.";
      env.toast(m);
      env.say(m);
      const status = (e as { status?: number }).status;
      if (copy !== null && (status === 404 || status === 409)) env.draftGone(copy);   // that copy is gone: don't keep showing it
      env.draftsChanged();
      return;
    }
    if (turn !== resumeTurn) return;
    if (c) return env.toast(busy);   // one opened while this loaded is never replaced
    const note = resumeNotice(d.notes);
    open({ ...(resumeOpts(d) as OpenOptions), body: d.text ?? "", attachments: (d.files ?? []).slice(), note });
    if (note) env.say(note);   // in the composer, not a toast under its backdrop: it stays until it closes
  }

  /* ---------- the AI's words ---------- */
  const aiOffered = (x: Pick<Composer, "mode" | "origKey" | "resume" | "quote">) => (x.mode === "reply" || x.mode === "replyall") && x.origKey !== null && !resumeHidesAi(x);

  function placeBody(x: Composer, body: string) {
    x.body = body;
    markEdited(x);
    retireGuard(x);   // a scripted write fires no input event
  }

  async function aiDraft(opts: { tone?: DraftTone; instruction?: string; unless?: string; quiet?: boolean } = {}) {
    const x = c;
    if (!x) return;
    if (x.origKey === null) return env.toast("Open a reply to draft one");
    if (x.ai) return;   // a generation is already in flight for this composer
    x.ai = "draft";
    publish();
    try {
      const d = await env.post("/api/ai/draft-reply", { key: x.origKey, account: x.from, tone: opts.tone ?? env.tone(), instruction: opts.instruction ?? "" }, !opts.quiet);
      if (c !== x) return;   // closed (or replaced by another reply) while drafting: not this composer's text
      if (opts.unless !== undefined && x.body !== opts.unless) return;   // a quick reply you've started editing: yours wins
      const had = x.body.trim() && x.body.trim() !== x.sigBlock.trim();
      if (had) x.rwUndo = { value: x.body, sel: env.selection() };   // what you'd written is one Undo away
      placeBody(x, d.body + x.sigBlock);   // the signature stays below the AI's reply
      publish();
      env.focus("body");
      env.toast("Draft ready — review and edit before sending");
    } catch (e) {
      if (c === x && !opts.quiet) env.toast(`Couldn’t draft: ${e instanceof Error ? e.message : ""}`);
    } finally {
      x.ai = "";
      publish(x);
    }
  }

  async function aiRewrite(op: RewriteOp) {
    const x = c;
    if (!x || x.ai) return;
    const full = x.body, [s, e] = env.selection(), hasSel = e > s, text = hasSel ? full.slice(s, e) : full;
    if (!text.trim()) return env.toast("Write something to rewrite first");
    let lang = "English";
    if (op === "translate") {
      lang = (env.prompt("Translate to which language?", "Spanish") ?? "").trim();
      if (!lang) return;
    }
    x.ai = "rewrite";
    publish();
    try {
      const d = await env.post("/api/ai/rewrite", { text, op, lang }, true);
      if (c !== x) return;   // closed while rewriting: the box now belongs to another composer, or none
      x.rwUndo = { value: full, sel: [s, e] };   // one step back to what it was
      placeBody(x, hasSel ? full.slice(0, s) + d.text + full.slice(e) : d.text);
      publish();
      env.focus("body");
      if (hasSel) env.select(s, s + String(d.text).length);
      env.toast(`${hasSel ? "Selection" : "Message"} rewritten`);
    } catch (err) {
      if (c === x) env.toast(`Couldn’t rewrite: ${err instanceof Error ? err.message : ""}`);
    } finally {
      x.ai = "";
      publish(x);
    }
  }

  function rewriteUndo() {
    const x = c;
    if (!x?.rwUndo) return;
    const { value, sel } = x.rwUndo;
    x.body = value;
    x.rwUndo = null;
    retireGuard(x);
    publish();
    env.focus("body");
    env.select(sel[0], sel[1]);
    env.toast("Back to what you had");
  }

  async function quickReply(to: Copy, kind: string) {
    const q = QUICK.find((x) => x.key === kind);
    if (!q || c) return;
    const x = await reply(to);
    if (!x || !stillOpen(x)) return;
    const start = q.line + x.sigBlock;
    x.body = start;   // not an edit: nothing to save until you touch it
    publish();
    env.focus("body");
    env.select(q.line.length, q.line.length);
    void aiDraft({ tone: "brief", instruction: `${q.ask} One or two sentences.`, unless: start, quiet: true });   // no AI → the line stays, silently
  }

  // Always ask again: the server checks the thread hasn't moved on, so a stale draft is never put in the box.
  async function openDraftAhead(to: Copy, forget: () => void) {
    if (openingAhead === to.key || c) return;
    openingAhead = to.key;
    try {
      const x = await reply(to);
      if (!x || !stillOpen(x)) return;
      let body: string | null = null;
      try {
        const d = await env.get(`/api/ai/draft-ahead?key=${to.key}&folder=${encodeURIComponent(to.folder)}&uid=${to.uid}&peek=1`);
        if (d?.status === "ready" && d.body) body = d.body;
      } catch { /* treated as not fresh */ }
      if (!stillOpen(x)) return;
      if (body !== null) {
        placeBody(x, body + x.sigBlock);
        publish();
        env.focus("body");
        env.toast("Draft ready — review and edit before sending");
      } else {
        forget();
        env.toast("Thread moved on — drafting a fresh reply…");
        void aiDraft();
      }
    } finally {
      openingAhead = null;
    }
  }

  return {
    view, float, laterOpen,
    isOpen: () => c !== null,
    open, close, discard, save, autosaveTick, send, makeRoom,
    edit, setFrom, setTyped, commitChip, removeChip, showCopies, addFiles, removeAttachment,
    dismissGuard, confirmGuard, guardFix,
    reply, forward, editDraft, quickReply, openDraftAhead, queued,
    aiOffered, aiDraft, aiRewrite, rewriteUndo,
    isNew: (x: Composer, a: string) => isNewToYou(x, a, env.mine()),
    tones: DRAFT_TONES,
  };
}

export type Composing = ReturnType<typeof createComposing>;

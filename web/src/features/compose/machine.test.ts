import { beforeEach, describe, expect, it, vi } from "vitest";
import { LIMITS } from "../../lib/api/limits.gen";
import { createStore } from "../../lib/store";
import { createComposing, type Answer, type Env, type Focus } from "./machine";

type Json = Answer;   // what a test reads off a request or an answer, field by field

function deferred<T>() {
  let resolve!: (v: T) => void, reject!: (e: unknown) => void;
  const promise = new Promise<T>((a, b) => { resolve = a; reject = b; });
  return { promise, resolve, reject };
}

const FROM = "ada@example.test";
const flush = async () => { for (let i = 0; i < 12; i++) await Promise.resolve(); };

function setup(over: Partial<Env> = {}) {
  const calls: { path: string; body: Json }[] = [];
  const toasts: string[] = [], said: string[] = [], focused: Focus[] = [];
  let clock = 0;
  const answers = new Map<string, ((body: Json) => Json)[]>();
  const answer = (path: string, fn: (body: Json) => Json) => answers.set(path, [...(answers.get(path) ?? []), fn]);
  const env: Env = {
    createStore,
    post: vi.fn(async (path: string, body: unknown) => {
      calls.push({ path, body });
      const q = answers.get(path);
      if (q?.length) return q.shift()!(body);
      if (path === "/api/draft") return { ok: true, draft_id: 7 };
      if (path === "/api/send") return { ok: true, outbox_id: 9, recipients: 1 };
      if (path === "/api/outbox/cancel") return { ok: true };
      if (path === "/api/contacts/known") return { ok: true, thin: false, items: {} };
      return { ok: true };
    }),
    get: vi.fn(async () => ({ ok: true })),
    toast: (m) => void toasts.push(m),
    say: (m) => void said.push(m),
    senders: () => [FROM],
    current: () => FROM,
    mine: () => new Set([FROM]),
    signature: () => "",
    tone: () => "balanced",
    limits: LIMITS,
    focusHere: () => null,
    focusBack: (_w, fallback) => void focused.push(fallback),
    focus: (f) => void focused.push(f),
    pickFile: () => {},
    readFile: async () => "AAAA",
    prompt: () => "Spanish",
    selection: () => [0, 0],
    select: () => {},
    draftsChanged: () => {},
    foldersChanged: () => {},
    outboxChanged: () => {},
    sent: () => {},
    draftGone: () => {},
    now: () => 1_000_000,
    clock: () => clock,
    ...over,
  };
  const m = createComposing(env);
  const posted = (path: string) => calls.filter((c) => c.path === path).map((c) => c.body);
  return { m, env, calls, posted, toasts, said, focused, answer, tick: (ms: number) => { clock += ms; } };
}

beforeEach(() => {
  vi.useFakeTimers();
  return () => vi.useRealTimers();
});

describe("opening", () => {
  it("says so when nothing can send", () => {
    const { m, toasts } = setup({ senders: () => [] });
    expect(m.open()).toBeNull();
    expect(toasts[0]).toMatch(/No sending account/);
  });

  it("starts above the signature, and only a fresh message gets one", () => {
    const { m } = setup({ signature: () => "Ada" });
    expect(m.open()!.body).toBe("\n\n-- \nAda");
    expect(m.open({ noSig: true })!.body).toBe("");
    expect(m.open({ draftId: 3 })!.body).toBe("");
  });

  it("swaps the signature as From changes, and leaves one you edited", () => {
    const selected: number[][] = [];
    const { m } = setup({ signature: (e) => (e === "bea@example.test" ? "Bea" : e === "cy@example.test" ? "" : "Ada"), selection: () => [5, 99], select: (a, b) => void selected.push([a, b]) });
    const c = m.open()!;
    m.edit("body", `Hello${c.sigBlock}`);
    m.setFrom("bea@example.test");
    expect(c.body).toBe("Hello\n\n-- \nBea");
    expect(selected.at(-1)).toEqual([5, 5]);
    m.setFrom("cy@example.test");
    expect(c.body).toBe("Hello");
    m.setFrom("bea@example.test");
    expect(c.body).toBe("Hello\n\n-- \nBea");
    m.edit("body", "Hello\n\n-- \nBea, edited");
    m.setFrom("ada@example.test");
    expect(c.body).toBe("Hello\n\n-- \nBea, edited");
  });

  it("tracks the signature a reopened text ends with, and none it doesn't", () => {
    const { m } = setup({ signature: () => "Ada" });
    expect(m.open({ body: "hi\n\n-- \nAda" })!.sigBlock).toBe("\n\n-- \nAda");
    expect(m.open({ body: "hi" })!.sigBlock).toBe("");
  });

  it("isn't dirty until an edit, so Esc on a prefilled reply leaves no draft", async () => {
    const { m, posted } = setup();
    m.open({ mode: "reply", to: ["bea@example.test"], subject: "Re: x" });
    m.close();
    await flush();
    expect(posted("/api/draft")).toEqual([]);
  });
});

describe("saving", () => {
  it("won't litter Drafts with a signature and nothing else", async () => {
    const { m, posted, toasts } = setup({ signature: () => "Ada" });
    m.open();
    expect(await m.save()).toBeNull();
    expect(toasts).toEqual(["Nothing to save yet"]);
    expect(posted("/api/draft")).toEqual([]);
  });

  it("saves in turn, each naming the copy the last one left", async () => {
    const { m, env, posted } = setup();
    const first = deferred<Json>(), seen: Json[] = [];
    vi.mocked(env.post).mockImplementationOnce(async (_path: string, body: unknown) => {
      seen.push(body);
      return first.promise;
    });
    m.open();
    m.edit("subject", "one");
    const a = m.save(true);
    m.edit("subject", "two");
    const b = m.save(true);
    await flush();
    expect(seen.length).toBe(1);
    expect(seen[0].draft_id).toBeNull();
    expect(posted("/api/draft")).toEqual([]);   // the second waits for the first
    first.resolve({ ok: true, draft_id: 41 });
    await a;
    await b;
    const second = posted("/api/draft")[0];
    expect(second.subject).toBe("two");
    expect(second.draft_id).toBe(41);
  });

  it("stays dirty when an edit landed during the save", async () => {
    const { m, env } = setup();
    const gate = deferred<Json>();
    vi.mocked(env.post).mockImplementationOnce(() => gate.promise);
    const c = m.open()!;
    m.edit("subject", "one");
    const saved = m.save(true);
    m.edit("subject", "one more");
    gate.resolve({ ok: true, draft_id: 1 });
    await saved;
    expect(c.dirty).toBe(true);
    const again = m.save(true);
    await again;
    expect(c.dirty).toBe(false);
  });

  it("clears dirty when nothing moved, and shows when it saved", async () => {
    const { m } = setup();
    const c = m.open()!;
    m.edit("body", "hello");
    await m.save(true);
    expect(c.dirty).toBe(false);
    expect(c.savedLine).toMatch(/^Saved /);
    expect(c.draftId).toBe(7);
  });

  it("replaces an older Saved when one fails", async () => {
    const { m, answer } = setup();
    const c = m.open()!;
    m.edit("body", "hello");
    await m.save(true);
    answer("/api/draft", () => { throw new Error("nope"); });
    m.edit("body", "hello again");
    expect(await m.save(true)).toBe(false);
    expect(c.savedLine).toBe("Not saved");
    expect(c.dirty).toBe(true);
  });

  it("commits a half-typed address to a chip", async () => {
    const { m, posted } = setup();
    m.open();
    m.setTyped("to", "bea@example.test");
    await m.save(true);
    expect(posted("/api/draft")[0].to).toEqual(["bea@example.test"]);
    expect(m.view.get()!.typed.to).toBe("");
  });

  it("adopts a forward's files at its first save, so the server stops adding them", async () => {
    const { m, answer, posted } = setup();
    const file = { filename: "a.pdf", content_type: "application/pdf", size: 3, b64: "AAA" };
    answer("/api/draft", () => ({ ok: true, draft_id: 5, resume: { in_reply_to: "<x@y>", references: "<x@y>", tail_text: "\n\nquoted" }, files: [file] }));
    const c = m.open({ mode: "forward", fwdAttach: 1, origKey: 3, subject: "Fwd: x" })!;
    m.edit("body", "see this");
    await m.save(true);
    expect(c.fwdAttach).toBe(0);
    expect(c.attachments).toEqual([file]);
    m.edit("body", "see this too");
    await m.save(true);
    const second = posted("/api/draft")[1];
    expect(second.resume.tail_text).toBe("\n\nquoted");
    expect(second.attachments).toEqual([{ filename: "a.pdf", content_type: "application/pdf", b64: "AAA" }]);
  });

  it("says an autosave is one, and from the second save on names the version it saw", async () => {
    const { m, answer, posted } = setup();
    answer("/api/draft", () => ({ ok: true, draft_id: 7, version: 1, pushed: false }));
    m.open();
    m.edit("body", "hello");
    await vi.advanceTimersByTimeAsync(8000);
    const [first] = posted("/api/draft");
    expect(first.autosave).toBe(true);
    expect(first.base_version).toBeUndefined();
    answer("/api/draft", () => ({ ok: true, draft_id: 7, version: 2, pushed: true }));
    m.edit("body", "hello again");
    await m.save();
    const second = posted("/api/draft")[1];
    expect(second.autosave).toBeUndefined();
    expect(second.base_version).toBe(1);
  });

  it("stops at a save it never saw, and only a save someone asks for overwrites it", async () => {
    const { m, answer, posted, toasts } = setup();
    answer("/api/draft", () => ({ ok: true, draft_id: 7, version: 1, pushed: true }));
    const c = m.open()!;
    m.edit("body", "one");
    await m.save(true);
    const conflict = () => { throw Object.assign(new Error("This draft changed in another window."), { status: 409 }); };
    answer("/api/draft", conflict);
    m.edit("body", "two");
    expect(await m.save()).toBe(false);
    expect(c.conflict).toBe(true);
    expect(c.savedLine).toBe("Changed in another window");
    expect(toasts.at(-1)).toContain("Save again");
    m.edit("body", "three");
    await vi.advanceTimersByTimeAsync(8000);
    expect(posted("/api/draft").length).toBe(2);           // autosave leaves it be
    answer("/api/draft", conflict);
    expect(await m.save(true)).toBe(false);                // so does a close, which nobody watches
    expect(posted("/api/draft")[2].base_version).toBe(1);
    answer("/api/draft", () => ({ ok: true, draft_id: 7, version: 3, pushed: true }));
    expect(await m.save()).toBe(true);
    expect(posted("/api/draft")[3].base_version).toBeUndefined();
    expect(c.conflict).toBe(false);
    expect(c.version).toBe(3);
  });

  it("tells the tray when a save someone asked for waits for its server", async () => {
    const { m, env, answer } = setup();
    const told = vi.spyOn(env, "outboxChanged");
    answer("/api/draft", () => ({ ok: true, draft_id: 7, version: 1, pushed: false }));
    m.open();
    m.edit("body", "one");
    await vi.advanceTimersByTimeAsync(8000);
    expect(told).not.toHaveBeenCalled();                   // an autosave always waits
    answer("/api/draft", () => ({ ok: true, draft_id: 7, version: 2, pushed: false }));
    m.edit("body", "two");
    await m.save();
    expect(told).toHaveBeenCalledTimes(1);
  });

  it("autosaves what changed, not what didn't, and not during a save", async () => {
    const { m, posted } = setup();
    const c = m.open()!;
    await vi.advanceTimersByTimeAsync(8000);
    expect(posted("/api/draft")).toEqual([]);
    m.edit("body", "hello");
    await vi.advanceTimersByTimeAsync(8000);
    expect(posted("/api/draft").length).toBe(1);
    expect(c.dirty).toBe(false);
    await vi.advanceTimersByTimeAsync(8000);
    expect(posted("/api/draft").length).toBe(1);
  });

  it("autosave leaves a half-typed address where it is, and closing takes it", async () => {
    const { m, posted } = setup();
    m.open({ subject: "x" });
    m.setTyped("to", "bea@exam");
    await vi.advanceTimersByTimeAsync(8000);
    expect(m.view.get()!.typed.to).toBe("bea@exam");
    expect(posted("/api/draft")[0].to).toEqual([]);
    m.setTyped("to", "bea@example.test");
    m.close();
    await flush();
    expect(posted("/api/draft").at(-1).to).toEqual(["bea@example.test"]);
  });

  it("stops autosaving once closed", async () => {
    const { m, posted } = setup();
    m.open();
    m.edit("body", "hello");
    m.close(true);
    await vi.advanceTimersByTimeAsync(20000);
    expect(posted("/api/draft")).toEqual([]);
  });
});

describe("closing and discarding", () => {
  it("saves a dirty composer as it closes", async () => {
    const { m, posted, toasts } = setup();
    m.open();
    m.edit("body", "hello");
    m.close();
    expect(m.view.get()).toBeNull();
    await flush();
    expect(posted("/api/draft").length).toBe(1);
    expect(toasts).toContain("Saved to Drafts");
  });

  it("keeps the words a click away when the closing save fails", async () => {
    const { m, answer } = setup();
    answer("/api/draft", () => { throw new Error("no"); });
    m.open({ subject: "keep me" });
    m.edit("body", "my words");
    m.close();
    await flush();
    const f = m.float.get();
    expect(f?.kind).toBe("failed");
    if (f?.kind !== "failed") return;
    f.edit();
    await flush();
    const c = m.view.get()!;
    expect(c.body).toBe("my words");
    expect(c.subject).toBe("keep me");
    expect(c.dirty).toBe(true);
    expect(m.float.get()).toBeNull();
  });

  it("waits for a save before discarding, so its copy doesn't stay behind", async () => {
    const { m, env, posted } = setup();
    const gate = deferred<Json>();
    vi.mocked(env.post).mockImplementationOnce(() => gate.promise);
    m.open();
    m.edit("body", "hello");
    void m.save(true);
    const gone = m.discard();
    await flush();
    expect(posted("/api/draft")).toEqual([]);
    gate.resolve({ ok: true, draft_id: 12 });
    await gone;
    expect(posted("/api/draft")).toEqual([{ draft_id: 12, delete: true }]);
    expect(m.view.get()).toBeNull();
  });

  it("makes room by saving first, and stays open if that fails", async () => {
    const { m, answer } = setup();
    const c = m.open()!;
    m.edit("body", "hello");
    answer("/api/draft", () => { throw new Error("no"); });
    let opened = false;
    expect(await m.makeRoom(() => { opened = true; })).toBe(false);
    expect(opened).toBe(false);
    expect(m.view.get()!.id).toBe(c.id);
    expect(await m.makeRoom(() => { opened = true; })).toBe(true);
    expect(opened).toBe(true);
    expect(m.view.get()).toBeNull();
  });
});

describe("the send-time check", () => {
  const person = "bea@example.test";

  it("holds a send with nobody to send it to, and offers no way past", () => {
    const { m, posted } = setup();
    m.open({ subject: "x" });
    m.send();
    expect(m.view.get()!.guard!.hard).toBe(true);
    expect(m.view.get()!.guard!.keys).toEqual(["norcpt"]);
    expect(posted("/api/send")).toEqual([]);
  });

  it("names a chip that isn't an address, and puts it back in the input to fix", () => {
    const { m, focused } = setup();
    m.open({ subject: "x" });
    m.commitChip("to", "not an address");
    m.send();
    expect(m.view.get()!.guard!.lines[0]!.key).toBe("bad");
    m.guardFix("bad", "to");
    const c = m.view.get()!;
    expect(c.to).toEqual([]);
    expect(c.typed.to).toBe("not an address");
    expect(focused.at(-1)).toEqual({ field: "to" });
  });

  it("asks about an empty subject, won't confirm before it arms, and sends after", async () => {
    const { m, posted, tick } = setup();
    m.open({ to: [person] });
    m.edit("body", "hello");
    m.send();
    expect(m.view.get()!.guard!.keys).toEqual(["subject"]);
    m.confirmGuard();
    expect(posted("/api/send")).toEqual([]);
    tick(800);
    m.confirmGuard(true);   // a held key never confirms
    expect(posted("/api/send")).toEqual([]);
    m.confirmGuard();
    expect(m.view.get()).toBeNull();
    await flush();
    expect(posted("/api/send").length).toBe(1);
  });

  it("any edit retires the check, and Send asks again", () => {
    const { m } = setup();
    m.open({ to: [person] });
    m.send();
    expect(m.view.get()!.guard).not.toBeNull();
    m.edit("body", "hello");
    expect(m.view.get()!.guard).toBeNull();
  });

  it("Keep editing puts focus back where it was", () => {
    const { m, focused } = setup({ focusHere: () => "here" });
    m.open({ to: [person] });
    m.send();
    m.dismissGuard();
    expect(m.view.get()!.guard).toBeNull();
    expect(focused.at(-1)).toBe("body");   // the fallback the test's focusBack records
  });

  it("asks again when something changed under an armed check", () => {
    const { m, tick, said } = setup();
    const c = m.open({ to: [person] })!;
    m.send();
    tick(800);
    c.subject = "x";
    c.body = "see attached";   // as an AI edit lands: no keystroke retired the check
    m.confirmGuard();
    expect(said.at(-1)).toMatch(/^Something changed\./);
    expect(m.view.get()!.guard!.keys).toEqual(["attach"]);
  });

  it("trimming a reply-all's Cc makes it a plain reply", () => {
    const many = ["a", "b", "c", "d", "e", "f"].map((x) => `${x}@example.test`);
    const { m, said } = setup();
    m.open({ mode: "replyall", to: [person], cc: many, autoCc: many, subject: "Re: x", subject0: "Re: x" });
    m.edit("body", "hello");
    m.send();
    expect(m.view.get()!.guard!.keys).toContain("fanout");
    m.guardFix("trim");
    const c = m.view.get()!;
    expect(c.mode).toBe("reply");
    expect(c.cc).toEqual([]);
    expect(said.at(-1)).toMatch(/^Removed 6 people\. This reply goes to 1 person\./);
  });
});

describe("an address one typo from someone you write to", () => {
  const typo = (near = "john@example.test") => (b: Json) => ({ ok: true, thin: false, items: Object.fromEntries(b.addrs.map((a: string) => [a, { known: false, self: false, near }])) });

  it("holds the send, and the fix takes the address it reads as", async () => {
    const { m, answer, posted, focused, said } = setup();
    answer("/api/contacts/known", typo());
    m.open({ subject: "Plans" });
    m.commitChip("to", "Jon <jon@example.test>");
    await vi.advanceTimersByTimeAsync(200);
    m.send();
    const g = m.view.get()!.guard!;
    expect(g.keys).toEqual(["near"]);
    expect(posted("/api/send")).toEqual([]);
    expect(g.lines[0]!.text).toMatch(/^“jon@example.test” is one typo away from john@example.test/);
    m.guardFix(g.lines[0]!.fix!.kind, undefined, g.lines[0]!.fix!.swap);
    const c = m.view.get()!;
    expect(c.guard).toBeNull();
    expect(c.to.map((x) => x.v)).toEqual(["Jon <john@example.test>"]);
    expect(c.dirty).toBe(true);
    expect(focused.at(-1)).toBe("send");
    expect(said.at(-1)).toBe("Changed to john@example.test.");
    m.send();
    await flush();
    expect(posted("/api/send").length).toBe(1);
  });

  it("goes on Send anyway, and an Undo doesn't ask about it again", async () => {
    const { m, answer, posted, tick } = setup();
    answer("/api/contacts/known", typo());
    m.open({ subject: "Plans", to: ["jon@example.test"] });
    await vi.advanceTimersByTimeAsync(200);
    m.send();
    expect(m.view.get()!.guard!.keys).toEqual(["near"]);
    tick(800);
    m.confirmGuard();
    await flush();
    expect(posted("/api/send").length).toBe(1);
    const f = m.float.get();
    if (f?.kind !== "countdown") throw new Error("no countdown");
    answer("/api/contacts/known", typo());
    f.undo();
    await flush();
    await vi.advanceTimersByTimeAsync(200);
    m.send();
    await flush();
    expect(posted("/api/send").length).toBe(2);
  });

  it("asks again about someone not waved through before", async () => {
    const { m, answer, tick } = setup();
    answer("/api/contacts/known", typo());
    m.open({ subject: "Plans", to: ["jon@example.test"] });
    await vi.advanceTimersByTimeAsync(200);
    m.send();
    tick(800);
    m.confirmGuard();
    await flush();
    const f = m.float.get();
    if (f?.kind !== "countdown") throw new Error("no countdown");
    f.undo();
    await flush();
    answer("/api/contacts/known", typo("kate@example.test"));
    m.commitChip("cc", "kat@example.test");
    await vi.advanceTimersByTimeAsync(200);
    m.send();
    expect(m.view.get()!.guard!.keys).toEqual(["near"]);
  });
});

describe("sending", () => {
  const person = "bea@example.test";
  const ready = (m: ReturnType<typeof setup>["m"]) => {
    m.open({ to: [person], subject: "Hello" });
    m.edit("body", "hello");
    return m.view.get()!;
  };

  it("closes before anything is awaited, so a second press does nothing", async () => {
    const { m, posted } = setup();
    ready(m);
    m.send();
    expect(m.view.get()).toBeNull();
    m.send();
    await flush();
    expect(posted("/api/send").length).toBe(1);
  });

  it("names the copy a save in flight leaves", async () => {
    const { m, env, posted } = setup();
    const gate = deferred<Json>();
    vi.mocked(env.post).mockImplementationOnce(() => gate.promise);
    ready(m);
    void m.save(true);
    m.send();
    await flush();
    expect(posted("/api/send")).toEqual([]);
    gate.resolve({ ok: true, draft_id: 77 });
    await flush();
    expect(posted("/api/send")[0].draft_id).toBe(77);
  });

  it("sends a Later time and says so, with no Undo countdown", async () => {
    const { m, posted } = setup();
    ready(m);
    m.send(1_000_000 + 3600);
    await flush();
    expect(posted("/api/send")[0].send_after).toBe(1_000_000 + 3600);
    expect(m.float.get()?.kind).toBe("done");
  });

  it("counts the server's window with an Undo, and Undo brings the words back", async () => {
    const { m, posted, toasts } = setup({ limits: { ...LIMITS, undo_send_seconds: 6 } });
    ready(m);
    m.send();
    await flush();
    const f = m.float.get();
    expect(f).toMatchObject({ kind: "countdown", left: 6 });
    if (f?.kind !== "countdown") return;
    vi.advanceTimersByTime(3000);
    expect(m.float.get()).toMatchObject({ kind: "countdown", left: 3 });
    f.undo();
    await flush();
    expect(posted("/api/outbox/cancel")).toEqual([{ id: 9 }]);
    const c = m.view.get()!;
    expect(c.body).toBe("hello");
    expect(c.to.map((x) => x.v)).toEqual([person]);
    expect(c.dirty).toBe(true);
    expect(toasts).toContain("Send undone");
  });

  it("says too late when the server refuses the Undo with a 409, and Sent when it doesn't answer", async () => {
    for (const [thrown, text] of [[{ status: 409, message: "too late to cancel" }, "✓ Sent — too late to undo"], [new Error("offline"), "✓ Sent"]] as const) {
      const { m, answer } = setup();
      ready(m);
      m.send();
      await flush();
      const f = m.float.get();
      if (f?.kind !== "countdown") throw new Error("no countdown");
      answer("/api/outbox/cancel", () => { throw thrown; });
      f.undo();
      await flush();
      expect(m.float.get()).toMatchObject({ kind: "done", text });
      expect(m.view.get()).toBeNull();
    }
  });

  it("keeps the message a click away when the send fails", async () => {
    const { m, answer } = setup();
    answer("/api/send", () => { throw new Error("offline"); });
    ready(m);
    m.send();
    await flush();
    const f = m.float.get();
    expect(f).toMatchObject({ kind: "failed", text: "Send failed — offline" });
    if (f?.kind !== "failed") return;
    f.edit();
    await flush();
    expect(m.view.get()!.body).toBe("hello");
    expect(m.float.get()).toBeNull();
  });

  it("doesn't ask again after an Undo for what you already waved through", async () => {
    const { m, tick } = setup();
    m.open({ to: [person] });
    m.edit("body", "hello");
    m.send();
    tick(800);
    m.confirmGuard();
    await flush();
    const f = m.float.get();
    if (f?.kind !== "countdown") throw new Error("no countdown");
    f.undo();
    await flush();
    m.send();
    expect(m.view.get()).toBeNull();
  });
});

describe("new to you", () => {
  it("asks once for every queued address, and marks only who is new", async () => {
    const { m, answer, posted } = setup();
    answer("/api/contacts/known", (b) => ({ ok: true, thin: false, items: Object.fromEntries(b.addrs.map((a: string) => [a, { known: a.startsWith("old"), self: false }])) }));
    m.open();
    m.commitChip("to", "new@example.test, old@example.test");
    await vi.advanceTimersByTimeAsync(200);
    expect(posted("/api/contacts/known").length).toBe(1);
    const c = m.view.get()!;
    expect(m.isNew(c, "new@example.test")).toBe(true);
    expect(m.isNew(c, "old@example.test")).toBe(false);
  });

  it("marks nobody on an archive too thin to say", async () => {
    const { m, answer } = setup();
    answer("/api/contacts/known", () => ({ ok: true, thin: true, items: { "new@example.test": { known: false, self: false } } }));
    m.open();
    m.commitChip("to", "new@example.test");
    await vi.advanceTimersByTimeAsync(200);
    expect(m.isNew(m.view.get()!, "new@example.test")).toBe(false);
  });

  it("never asks about a tag of your own address, and doesn't call it new", async () => {
    const { m, posted } = setup();
    m.open();
    m.commitChip("to", "ada+news@example.test");
    await vi.advanceTimersByTimeAsync(200);
    expect(posted("/api/contacts/known")).toEqual([]);
    expect(m.isNew(m.view.get()!, "ada+news@example.test")).toBe(false);
  });

  it("takes a tag the server calls yours as yours", async () => {
    const { m, answer } = setup({ mine: () => new Set(["bea@example.test"]) });
    answer("/api/contacts/known", () => ({ ok: true, thin: false, items: { "x+y@alias.example.test": { known: true, self: true } } }));
    m.open();
    m.commitChip("to", "x+y@alias.example.test");
    await vi.advanceTimersByTimeAsync(200);
    expect(m.isNew(m.view.get()!, "x+y@alias.example.test")).toBe(false);
  });

  it("never asks about the thread's own people", async () => {
    const { m, posted } = setup();
    m.open({ participants: ["Bea@example.test"], to: ["bea@example.test"] });
    await vi.advanceTimersByTimeAsync(200);
    expect(posted("/api/contacts/known")).toEqual([]);
  });
});

describe("files", () => {
  const file = (name: string, size: number) => ({ name, size, type: "text/plain" }) as File;

  it("holds a send while a file is still being read", async () => {
    const gate = deferred<string>();
    const { m } = setup({ readFile: () => gate.promise });
    m.open({ to: ["bea@example.test"], subject: "x" });
    m.addFiles([file("a.txt", 3)]);
    m.send();
    expect(m.view.get()!.guard!.keys).toContain("reading");
    gate.resolve("AAA");
    await flush();
    const c = m.view.get()!;
    expect(c.attachments[0]).toMatchObject({ filename: "a.txt", b64: "AAA" });
    expect(c.guard).toBeNull();
    expect(c.dirty).toBe(true);
  });

  it("refuses what would pass the server's limit, and says what that is", () => {
    const { m, toasts } = setup({ limits: { ...LIMITS, attach_bytes: 10 * 1024 * 1024 } });
    m.open();
    m.addFiles([file("small.bin", 9 * 1024 * 1024), file("big.bin", 2 * 1024 * 1024)]);
    expect(toasts).toEqual(["Attachments too large (10 MB max)"]);
    expect(m.view.get()!.attachments.length + m.view.get()!.reading.size).toBe(1);
  });

  it("holds the attachments to the server's limit, as it words it", () => {
    const { m, toasts } = setup();
    m.open();
    m.addFiles([file("big.bin", LIMITS.attach_bytes + 1)]);
    expect(toasts).toEqual([`Attachments too large (${LIMITS.attach_bytes / 1048576} MB max)`]);
  });
});

describe("forwarding", () => {
  const copy = { key: 5, folder: "INBOX", uid: 2 };
  const reading = (attachments: Json[]) => ({ get: vi.fn(async () => ({ account: FROM, subject: "x", text: "", attachments })) });

  it("takes along what the server says travels, and no more", async () => {
    const { m } = setup(reading([{ travels: false, size: 3 }, { travels: true, size: 2 }]));
    expect((await m.forward(copy))!.fwdAttach).toBe(1);
  });

  it("still asks about \"see attached\" on a forward whose parts all stay behind", async () => {
    const { m } = setup(reading([{ travels: false, size: 3 }]));
    await m.forward(copy);
    m.commitChip("to", "bea@example.test");
    m.edit("subject", "Fwd: x");
    m.edit("body", "see attached");
    m.send();
    expect(m.view.get()!.guard!.keys).toEqual(["attach"]);
  });

  it("stays quiet when one of the forward's files does travel", async () => {
    const { m, posted } = setup(reading([{ travels: true, size: 3 }]));
    await m.forward(copy);
    m.commitChip("to", "bea@example.test");
    m.edit("subject", "Fwd: x");
    m.edit("body", "see attached");
    m.send();
    await flush();
    expect(posted("/api/send").length).toBe(1);
  });
});

describe("from the mail", () => {
  const mail = { ok: true, account: FROM, subject: "Lunch", from_addr: "bea@example.test", from_name: "Bea", to: "ada@example.test, cy@example.test", cc: "", text: "soon?", date: "today" };

  it("replies to the sender, and reply all copies the rest but you", async () => {
    const asked: string[] = [];
    const { m } = setup({ get: async (p: string) => { asked.push(p); return mail; } });
    const c = (await m.reply({ key: 5, folder: "INBOX", uid: 2 }))!;
    expect(c.to.map((x) => x.v)).toEqual(["bea@example.test"]);
    expect(c.cc).toEqual([]);
    expect(c.subject).toBe("Re: Lunch");
    expect(c.origKey).toBe(5);
    expect(asked).toEqual(["/api/message/body?key=5&folder=INBOX&uid=2"]);
    const d = (await m.reply({ key: 5, folder: "INBOX", uid: 2 }, true))!;
    expect(d.cc.map((x) => x.v)).toEqual(["cy@example.test"]);
    expect(d.autoCc).toEqual(["cy@example.test"]);
  });

  it("refuses to reopen a draft while one is open, and says so", async () => {
    const { m, toasts, posted } = setup();
    m.open();
    await m.editDraft({ key: 3, folder: "Drafts", uid: 1 });
    expect(toasts).toEqual(["Close the message you’re writing first"]);
    expect(posted("/api/draft/resume")).toEqual([]);
  });

  it("reopens a draft the list can't show by its id, knowing its version", async () => {
    const { m, answer, posted } = setup();
    answer("/api/draft/resume", () => ({ ok: true, draft_id: 4, version: 6, account: FROM, mode: "new", subject: "x", text: "Mine." }));
    await m.editDraft(null, 4);
    expect(posted("/api/draft/resume")).toEqual([{ draft_id: 4 }]);
    m.edit("body", "Mine, more.");
    await m.save();
    expect(posted("/api/draft")[0].base_version).toBe(6);
  });

  it("reopens a draft with its text and files, and saves nothing until an edit", async () => {
    const { m, answer, posted } = setup();
    answer("/api/draft/resume", () => ({ ok: true, draft_id: 4, account: FROM, mode: "reply", subject: "Re: x", to: ["bea@example.test"], cc: [], bcc: ["cy@example.test"], text: "mine", tail_text: "\n\nquoted", in_reply_to: "<a@b>", references: "<a@b>", original_key: 2, participants: [], files: [{ filename: "f", content_type: "x", size: 1, b64: "" }], notes: { plain: true } }));
    await m.editDraft({ key: 3, folder: "Drafts/Old & new", uid: 12 });
    expect(posted("/api/draft/resume")).toEqual([{ key: 3, folder: "Drafts/Old & new", uid: 12 }]);
    const c = m.view.get()!;
    expect(c.origKey).toBe(2);
    expect(c.body).toBe("mine");
    expect(c.dirty).toBe(false);
    expect(c.draftId).toBe(4);
    expect(c.showCc).toBe(true);
    expect(c.attachments.length).toBe(1);
    expect(c.quote!.text).toBe("quoted");
    expect(c.note).toMatch(/plain text/);
  });

  it("drops a row whose draft has gone", async () => {
    const gone = vi.fn();
    const { m, answer } = setup({ draftGone: gone });
    answer("/api/draft/resume", () => { throw Object.assign(new Error("gone"), { status: 409 }); });
    await m.editDraft({ key: 3, folder: "Drafts", uid: 1 });
    expect(gone).toHaveBeenCalledWith({ key: 3, folder: "Drafts", uid: 1 });
    expect(m.view.get()).toBeNull();
  });
});

describe("the AI's words", () => {
  const seed = { mode: "reply" as const, origKey: 4, to: ["bea@example.test"], subject: "Re: x" };

  it("keeps the signature below a draft, and what you wrote one Undo away", async () => {
    const { m, answer } = setup({ signature: () => "Ada" });
    answer("/api/ai/draft-reply", () => ({ ok: true, body: "Sure." }));
    m.open(seed);
    m.edit("body", "mine\n\n-- \nAda");
    await m.aiDraft();
    const c = m.view.get()!;
    expect(c.body).toBe("Sure.\n\n-- \nAda");
    expect(c.rwUndo!.value).toBe("mine\n\n-- \nAda");
    m.rewriteUndo();
    expect(m.view.get()!.body).toBe("mine\n\n-- \nAda");
  });

  it("leaves a composer alone that closed while drafting", async () => {
    const gate = deferred<Json>();
    const { m, env } = setup();
    vi.mocked(env.post).mockImplementationOnce(() => gate.promise);
    const first = m.open(seed)!;
    const asked = m.aiDraft();
    m.close(true);
    const second = m.open(seed)!;
    gate.resolve({ ok: true, body: "late" });
    await asked;
    expect(second.body).toBe("");
    expect(first.body).toBe("");
  });

  it("rewrites the selection only", async () => {
    const { m, answer } = setup({ selection: () => [0, 5] });
    answer("/api/ai/rewrite", (b) => ({ ok: true, text: b.text.toUpperCase() }));
    m.open(seed);
    m.edit("body", "hello world");
    await m.aiRewrite("formal");
    expect(m.view.get()!.body).toBe("HELLO world");
  });

  it("a quick reply starts as its line, and the AI's only replaces an untouched one", async () => {
    const { m, env } = setup({ get: async () => ({ ok: true, account: FROM, from_addr: "bea@example.test", subject: "x", text: "" }) });
    const gate = deferred<Json>();
    vi.mocked(env.post).mockImplementation(async (path: string) => (path === "/api/ai/draft-reply" ? gate.promise : { ok: true, items: {} }));
    await m.quickReply({ key: 4, folder: "INBOX", uid: 1 }, "yes");
    expect(m.view.get()!.body).toBe("Yes, that works for me.");
    expect(m.view.get()!.dirty).toBe(false);
    m.edit("body", "Yes, but later.");
    gate.resolve({ ok: true, body: "AI words" });
    await flush();
    expect(m.view.get()!.body).toBe("Yes, but later.");
  });

  it("offers a draft only on a reply that still has the original to read", () => {
    const { m } = setup();
    expect(m.aiOffered({ mode: "reply", origKey: 3, resume: null, quote: null })).toBe(true);
    expect(m.aiOffered({ mode: "new", origKey: null, resume: null, quote: null })).toBe(false);
    expect(m.aiOffered({ mode: "reply", origKey: 3, resume: { tail_text: "" }, quote: null })).toBe(false);
  });

  it("a stale draft ahead is never put in the box", async () => {
    const forget = vi.fn();
    const asked: string[] = [];
    const { m, toasts, answer } = setup({ get: async (p: string) => { asked.push(p); return p.includes("draft-ahead") ? { ok: true, status: "none" } : { ok: true, account: FROM, from_addr: "bea@example.test", subject: "x" }; } });
    answer("/api/ai/draft-reply", () => ({ ok: true, body: "fresh" }));
    await m.openDraftAhead({ key: 4, folder: "INBOX", uid: 6 }, forget);
    await flush();
    expect(asked).toEqual(["/api/message/body?key=4&folder=INBOX&uid=6", "/api/ai/draft-ahead?key=4&folder=INBOX&uid=6&peek=1"]);
    expect(forget).toHaveBeenCalled();
    expect(toasts[0]).toMatch(/moved on/);
    expect(m.view.get()!.body).toBe("fresh");
  });
});

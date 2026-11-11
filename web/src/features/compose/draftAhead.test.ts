import { act } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Copy } from "../../lib/copy";
import { cleanup, renderHook, type Rendered } from "../../lib/testing/renderHook";

const the = vi.hoisted(() => ({
  get: vi.fn(), announce: vi.fn(), composing: false, openDraftAhead: vi.fn(), reply: vi.fn(),
  forgetOnSend: undefined as ((key: number) => void) | undefined,
}));
vi.mock("../../lib/api/client", () => ({ get: the.get }));
vi.mock("../../lib/toast", () => ({ announce: the.announce }));
vi.mock("./compose", () => ({
  composing: { isOpen: () => the.composing, openDraftAhead: the.openDraftAhead, reply: the.reply },
  onForgetDraftAhead: (fn: (key: number) => void) => void (the.forgetOnSend = fn),
}));

const K: Copy = { key: 7, folder: "INBOX", uid: 70 }, L: Copy = { key: 8, folder: "INBOX", uid: 80 };
const page = { hidden: false };

// What the server says of a written reply, a call at a time: a peek, or a read that may ask for one.
type Said = "none" | "generating" | "ready";
const server = { peek: "none" as Said, read: [] as Said[] };
const answer = (status: Said) => (status === "ready" ? { ok: true, status, body: "Thanks!", tone: null } : { ok: true, status });
const peeks = () => the.get.mock.calls.filter(([p]) => (p as string).endsWith("&peek=1")).length;
const reads = () => the.get.mock.calls.filter(([p]) => !(p as string).endsWith("&peek=1")).length;
const later = (ms: number) => act(async () => void (await vi.advanceTimersByTimeAsync(ms)));

let draftAhead: typeof import("./draftAhead");
interface On { copy: Copy | null; wanted: boolean }
// The reader on a message: what it asks for, and the chip it shows.
const reading = (copy: Copy | null, wanted = true): Promise<Rendered<On, string | undefined>> =>
  renderHook(({ copy, wanted }: On) => {
    draftAhead.useDraftAhead(copy, wanted, false);
    return draftAhead.useDraftChip(copy?.key ?? null);
  }, { props: { copy, wanted } });

beforeEach(async () => {
  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
  Object.assign(server, { peek: "none", read: [] });
  Object.assign(the, { composing: false });
  page.hidden = false;
  [the.get, the.announce, the.openDraftAhead, the.reply].forEach((fn) => fn.mockReset());
  the.get.mockImplementation(async (path: string) => answer(path.endsWith("&peek=1") ? server.peek : server.read.shift() ?? "none"));
  vi.stubGlobal("document", { get hidden() { return page.hidden; } });
  vi.resetModules();
  draftAhead = await import("./draftAhead");
});
afterEach(async () => {
  await cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("opening a message", () => {
  it("only peeks, which never spends, and shows a reply already written", async () => {
    server.peek = "ready";
    const r = await reading(K, false);
    await later(0);
    expect(the.get).toHaveBeenCalledWith("/api/ai/draft-ahead?key=7&folder=INBOX&uid=70&peek=1");
    expect(r.result).toBe("ready");
    await later(5000);
    expect(reads()).toBe(0);
  });
  it("asks for nothing on a fast skim", async () => {
    const r = await reading(K);
    await later(1000);
    await r.rerender({ copy: L, wanted: true });
    await later(1000);
    await r.unmount();
    await later(5000);
    expect(reads()).toBe(0);
    expect(peeks()).toBe(2);
  });
  it("asks for a reply once you stay on it a moment, and says it's drafting until it's ready", async () => {
    server.read = ["generating", "ready"];
    const r = await reading(K);
    await later(1200);
    expect(reads()).toBe(1);
    expect(r.result).toBe("drafting");
    await later(10_000);
    expect(r.result).toBe("ready");
    expect(the.announce).toHaveBeenCalledTimes(1);
    expect(the.announce).toHaveBeenCalledWith("AI reply draft ready — press r to review");
  });
  it("asks for no reply on mail that doesn't want one", async () => {
    await reading(K, false);
    await later(5000);
    expect(reads()).toBe(0);
  });
  it("waits on a reply the server is already writing, without asking for another", async () => {
    server.peek = "generating";
    server.read = ["generating", "ready"];
    const r = await reading(K);
    await later(0);
    expect(r.result).toBe("drafting");
    await later(1200);
    expect(reads()).toBe(1);
    await later(10_000);
    expect(r.result).toBe("ready");
  });
  it("gives up after two waits rather than leave Drafting… standing", async () => {
    server.read = ["generating", "generating", "generating", "generating"];
    const r = await reading(K);
    await later(1200);
    expect(r.result).toBe("drafting");
    await later(30_000);
    expect(reads()).toBe(3);
    expect(r.result).toBeUndefined();
  });
  it("says nothing of a reply while you're writing one", async () => {
    server.read = ["ready"];
    the.composing = true;
    await reading(K);
    await later(5000);
    expect(reads()).toBe(0);
    expect(the.announce).not.toHaveBeenCalled();
  });
  // A known bug: a wait that comes due once you have moved off lets nothing go, and coming back neither drops "drafting" on a
  // peek that says "none" nor starts a wait again, so the chip says Drafting… until the page reloads.
  it("doesn’t still say Drafting… on coming back to a message whose reply failed while you were away", async () => {
    server.read = ["generating"];
    const r = await reading(K);
    await later(1200);
    expect(r.result).toBe("drafting");
    await r.rerender({ copy: L, wanted: false });
    await later(10_000);
    server.peek = "none";     // the server gave up on it meanwhile
    await r.rerender({ copy: K, wanted: true });
    await later(30_000);
    expect(r.result).toBeUndefined();
  });
});

describe("the stream saying a reply is ready", () => {
  it("reads it again rather than trust what the stream said", async () => {
    const r = await reading(K, false);
    await later(0);
    server.read = ["ready"];
    await act(async () => draftAhead.onDraftReady(K.key));
    await later(0);
    expect(reads()).toBe(1);
    expect(r.result).toBe("ready");
  });
  it("peeks at the open message when the reply was for another in its thread", async () => {
    await reading(K, false);
    await later(0);
    server.peek = "ready";
    await act(async () => draftAhead.onDraftReady(99));
    await later(0);
    expect(peeks()).toBe(2);
    expect(reads()).toBe(0);
  });
  it("is ignored in a tab nobody is looking at, and without a key", async () => {
    await reading(K, false);
    await later(0);
    page.hidden = true;
    draftAhead.onDraftReady(K.key);
    page.hidden = false;
    draftAhead.onDraftReady(null);
    draftAhead.onDraftReady(undefined);
    await later(0);
    expect(the.get).toHaveBeenCalledTimes(1);
  });
});

describe("the brief's cards", () => {
  it("peek once a message, and only those not known ready", async () => {
    server.peek = "ready";
    await draftAhead.peekCards([K, L]);
    await draftAhead.peekCards([K, L]);
    expect(peeks()).toBe(2);
    const r = await renderHook(() => draftAhead.useDraftChip(K.key));
    expect(r.result).toBe("ready");
  });
  it("stay a plain Reply when a peek fails", async () => {
    the.get.mockRejectedValue(new Error("down"));
    await expect(draftAhead.peekCards([K])).resolves.toBeUndefined();
    expect((await renderHook(() => draftAhead.useDraftChip(K.key))).result).toBeUndefined();
  });
});

describe("r", () => {
  it("opens the reply written ahead when there is one, and a plain reply when not", async () => {
    draftAhead.answer(K);
    expect(the.reply).toHaveBeenCalledWith(K);
    server.peek = "ready";
    await draftAhead.peekCards([K]);
    draftAhead.answer(K);
    expect(the.openDraftAhead).toHaveBeenCalledWith(K, expect.any(Function));
  });
  it("doesn't offer it again once the reply has gone", async () => {
    server.peek = "ready";
    await draftAhead.peekCards([K]);
    the.forgetOnSend!(K.key);
    draftAhead.answer(K);
    expect(the.openDraftAhead).not.toHaveBeenCalled();
    expect(the.reply).toHaveBeenCalledWith(K);
  });
});

import type { QueryClient } from "@tanstack/react-query";
import { act } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ListData, ListPage } from "../features/mailbox/data";
import { cleanup, renderHook, testClient } from "../lib/testing/renderHook";

const heard = vi.hoisted(() => ({ newMail: vi.fn(), snoozeWake: vi.fn(), draftReady: vi.fn() }));
vi.mock("../features/assistant/NewMail", () => ({ onNewMail: heard.newMail, onSnoozeWake: heard.snoozeWake }));
vi.mock("../features/compose/draftAhead", () => ({ onDraftReady: heard.draftReady }));

// The server's stream as a test drives it: what it sends, and when it drops and comes back.
class Stream {
  static last: Stream | undefined;
  readonly listeners = new Map<string, ((e: MessageEvent<string>) => void)[]>();
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  closed = false;
  readonly url: string;
  constructor(url: string) {
    this.url = url;
    Stream.last = this;
  }
  addEventListener(topic: string, fn: (e: MessageEvent<string>) => void) {
    this.listeners.set(topic, [...(this.listeners.get(topic) ?? []), fn]);
  }
  close() {
    this.closed = true;
  }
}
const stream = () => Stream.last!;
const send = (topic: string, data: object = {}) => act(async () => {
  for (const fn of stream().listeners.get(topic) ?? []) fn({ data: JSON.stringify(data) } as MessageEvent<string>);
});
const drops = () => act(async () => stream().onerror?.());
const comesBack = () => act(async () => stream().onopen?.());

const page = { hidden: false };
let qc: QueryClient;
const reads = () => vi.mocked(qc.invalidateQueries).mock.calls.map(([f]) => [(f!.queryKey as string[])[0], f!.refetchType]);
const readKeys = () => reads().map(([key]) => key);

async function open(build = "b1") {
  vi.resetModules();
  vi.stubGlobal("document", { get hidden() { return page.hidden; }, querySelector: () => ({ content: build }) });
  vi.stubGlobal("location", { pathname: "/all", search: "" });
  vi.stubGlobal("EventSource", Stream);
  const realtime = await import("./realtime");
  qc = testClient();
  vi.spyOn(qc, "invalidateQueries");
  await renderHook(() => realtime.useRealtime(), { client: qc });
  return realtime;
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "setInterval", "clearInterval"] });
  page.hidden = false;
  Stream.last = undefined;
  Object.values(heard).forEach((fn) => fn.mockReset());
});
afterEach(async () => {
  await cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("what the stream says changed", () => {
  it("listens on the page's one stream", async () => {
    await open();
    expect(stream().url).toBe("/api/events");
  });
  it("new mail reads the lists again at once, and the digest a moment later", async () => {
    await open();
    await send("new-mail", { account: "a@example.test", folder: "INBOX", count: 1 });
    expect(readKeys()).toEqual(["messages", "folders", "screener", "tidy"]);
    await act(async () => vi.advanceTimersByTime(400));
    expect(readKeys()).toContain("digest");
  });
  it("reads the digest once for a burst of changes, not once a change", async () => {
    await open();
    for (const topic of ["flags-changed", "mute", "undo-done", "followup"]) {
      await send(topic);
      await act(async () => vi.advanceTimersByTime(100));
    }
    await act(async () => vi.advanceTimersByTime(400));
    expect(readKeys().filter((k) => k === "digest")).toHaveLength(1);
    expect(readKeys().filter((k) => k === "messages")).toHaveLength(4);
  });
  it("in a tab nobody is looking at, marks what changed and reads none of it until the tab is back", async () => {
    await open();
    page.hidden = true;
    await send("new-mail");
    expect(reads().every(([, how]) => how === "none")).toBe(true);
    page.hidden = false;
    await send("new-mail");
    expect(reads().at(-1)).toEqual(["tidy", "active"]);
  });
  it("a finished sync reads the counts, the documents and the labels again", async () => {
    await open();
    await send("sync-done");
    expect(readKeys()).toEqual(["digest", "folders", "documents", "labels"]);
  });
  it("the outbox and to-dos each read their own again", async () => {
    await open();
    await send("outbox");
    await send("tasks-changed");
    expect(readKeys()).toEqual(["outbox", "pending", "tasks"]);
  });
  it("hands new mail to the pill with where you are, whether the AI is on and the account's own name", async () => {
    await open();
    qc.setQueryData(["digest"], { ai: { engine: "api" }, aliases: { "a@example.test": "Ada" }, accounts: ["bo@example.test", "a@example.test"] });
    await send("new-mail", { account: "a@example.test", count: 2 });
    const [said, route, ai, nameOf, listed] = heard.newMail.mock.calls[0]!;
    expect(said).toEqual({ account: "a@example.test", count: 2 });
    expect(route.source).toEqual({ kind: "allInboxes" });
    expect(ai).toBe(true);
    expect(nameOf("a@example.test")).toBe("Ada");
    expect(nameOf("bo@example.test")).toBe("bo");
    expect(listed).toEqual(["bo@example.test", "a@example.test"]);     // whose place in it keys when each Inbox was looked at
  });
  it("says the AI is off when the digest says so, or hasn't come", async () => {
    await open();
    await send("new-mail");
    expect(heard.newMail.mock.calls[0]![2]).toBe(false);
    qc.setQueryData(["digest"], { ai: { engine: "none" }, aliases: {} });
    await send("new-mail");
    expect(heard.newMail.mock.calls[1]![2]).toBe(false);
  });
  it("passes woken snoozes and a written reply on by what they name", async () => {
    await open();
    await send("snooze-wake", { accounts: ["a@example.test"], count: 3 });
    expect(heard.snoozeWake).toHaveBeenCalledWith({ accounts: ["a@example.test"], count: 3 }, expect.objectContaining({ source: { kind: "allInboxes" } }), []);
    qc.setQueryData(["digest"], { ai: { engine: "none" }, accounts: ["a@example.test"] });
    await send("snooze-wake", { accounts: ["a@example.test"], count: 1 });
    expect(heard.snoozeWake).toHaveBeenLastCalledWith({ accounts: ["a@example.test"], count: 1 }, expect.objectContaining({ source: { kind: "allInboxes" } }), ["a@example.test"]);
    await send("draft-ready", { key: 42 });
    expect(heard.draftReady).toHaveBeenCalledWith(42);
  });
});

describe("a folder's read finishing", () => {
  const listOf = (...pages: Partial<ListPage>[]): ListData => ({
    pageParams: pages.map((_, i) => i),
    pages: pages.map((p) => ({ rows: [], hasMore: false, total: 0, syncing: false, syncFailed: false, catchingUp: false, ...p })),
  });

  it("reads the lists again when it went well", async () => {
    await open();
    await send("folder-synced", { account: "a@example.test", folder: "INBOX", ok: true });
    expect(readKeys()).toContain("messages");
  });
  it("says it failed on that folder's list and stays, rather than read it again for as long as the server says no", async () => {
    await open();
    const key = ["messages", "folder", "a@example.test", "Lists", true];
    qc.setQueryData(key, listOf({ syncing: true, catchingUp: true }, { syncing: false }));
    await send("folder-synced", { account: "a@example.test", folder: "Lists", ok: false });
    expect(qc.getQueryData<ListData>(key)!.pages.map((p) => [p.syncing, p.catchingUp, p.syncFailed])).toEqual([[false, false, true], [false, false, false]]);
    expect(qc.invalidateQueries).not.toHaveBeenCalled();
  });
  it("stops All inboxes waiting on it without calling that a failure, and leaves other folders be", async () => {
    await open();
    const all = ["messages", "all", false], other = ["messages", "folder", "a@example.test", "INBOX", true];
    qc.setQueryData(all, listOf({ syncing: true }));
    qc.setQueryData(other, listOf({ syncing: true }));
    await send("folder-synced", { account: "a@example.test", folder: "Lists", ok: false });
    expect(qc.getQueryData<ListData>(all)!.pages[0]).toMatchObject({ syncing: false, syncFailed: false });
    expect(qc.getQueryData<ListData>(other)!.pages[0]).toMatchObject({ syncing: true });
  });
});

describe("while the stream is down", () => {
  it("reads the outbox and the to-dos on a timer, and the digest on a slower one", async () => {
    await open();
    await comesBack();
    await drops();
    await act(async () => vi.advanceTimersByTime(20_000));
    expect(readKeys()).toEqual(["outbox", "pending"]);
    await act(async () => vi.advanceTimersByTime(25_000));
    expect(readKeys()).toEqual(["outbox", "pending", "outbox", "pending", "digest"]);
  });
  it("reads on the timer before the stream has ever opened", async () => {
    await open();
    await act(async () => vi.advanceTimersByTime(20_000));
    expect(readKeys()).toEqual(["outbox", "pending"]);
  });
  it("stops reading on the timer once the stream is back, and the server's resync reads everything again", async () => {
    await open();
    await drops();
    await act(async () => vi.advanceTimersByTime(20_000));
    await comesBack();
    vi.mocked(qc.invalidateQueries).mockClear();
    await act(async () => vi.advanceTimersByTime(90_000));
    expect(readKeys()).toEqual([]);
    await send("resync");
    await act(async () => vi.advanceTimersByTime(400));
    expect(readKeys()).toEqual(expect.arrayContaining(["messages", "folders", "screener", "tidy", "digest"]));
  });
  it("reads nothing on the timer in a tab nobody is looking at", async () => {
    await open();
    await drops();
    page.hidden = true;
    await act(async () => vi.advanceTimersByTime(90_000));
    expect(readKeys()).toEqual([]);
  });
});

describe("a tab left open", () => {
  it("offers a reload when the server is on another build", async () => {
    const { newBuild } = await open("b1");
    await send("hello", { build: "b1" });
    expect(newBuild.get()).toBe(false);
    await send("hello", {});
    expect(newBuild.get()).toBe(false);
    await send("hello", { build: "b2" });
    expect(newBuild.get()).toBe(true);
  });
  it("closes the stream and stops its timers when it goes", async () => {
    await open();
    await drops();
    await send("new-mail");
    vi.mocked(qc.invalidateQueries).mockClear();
    await cleanup();
    expect(stream().closed).toBe(true);
    await act(async () => vi.advanceTimersByTime(90_000));
    expect(readKeys()).toEqual([]);
  });
});

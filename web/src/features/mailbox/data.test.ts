import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ListSource } from "../../app/route";
import { ApiError } from "../../lib/api/client";
import type { Copy } from "../../lib/copy";
import { cleanup, renderHook, testClient, waitFor } from "../../lib/testing/renderHook";

const api = vi.hoisted(() => ({ get: vi.fn(), scope: "all" }));
vi.mock("../../lib/api/client", async (real) => ({ ...(await real<typeof import("../../lib/api/client")>()), get: api.get }));
vi.mock("../../lib/prefs", async () => {
  const { createStore } = await import("../../lib/store");
  const scope = createStore("all");
  return { pref: () => ({ ...scope, get: () => api.scope, useValue: () => api.scope }) };
});

const A = "ada@example.test";
const asked = () => api.get.mock.calls.map(([path]) => path as string);
const item = (key: number, over: object = {}) => ({
  key, uid: key * 10, account: A, folder: "INBOX", from_name: "Bo", from_addr: "bo@example.test", subject: `s${key}`, snippet: "", when: "9:00",
  bucket: null, ...over,
});
const page = (keys: number[], over: object = {}) => ({ items: keys.map((k) => item(k)), has_more: false, total: keys.length, ...over });

const load = async () => {
  vi.resetModules();
  return import("./data");
};

beforeEach(() => {
  api.get.mockReset();
  api.scope = "all";
});
afterEach(async () => {
  await cleanup();
  vi.unstubAllGlobals();
});

describe("making a body ready before it opens", () => {
  const copy: Copy = { key: 7, folder: "INBOX", uid: 70 };

  it("asks once a page for each copy", async () => {
    const { warmBody } = await load();
    api.get.mockResolvedValue({ prefetched: true });
    await warmBody(copy);
    await warmBody(copy);
    expect(asked()).toEqual(["/api/message/body?key=7&folder=INBOX&uid=70&prefetch=1"]);
  });
  it("asks again for one the server skipped, or that failed", async () => {
    const { warmBody } = await load();
    api.get.mockResolvedValueOnce({ prefetched: false }).mockRejectedValueOnce(new Error("down")).mockResolvedValue({ prefetched: true });
    await warmBody(copy);
    await warmBody(copy);
    await warmBody(copy);
    await warmBody(copy);
    expect(asked()).toHaveLength(3);
  });
  it("warms a conversation's newest dozen, newest first, one at a time", async () => {
    const { warmMembers } = await load();
    const members = Array.from({ length: 15 }, (_, i): Copy => ({ key: i + 1, folder: "INBOX", uid: i + 1 }));
    let inFlight = 0, most = 0;
    api.get.mockImplementation(async () => {
      most = Math.max(most, ++inFlight);
      await Promise.resolve();
      inFlight--;
      return { prefetched: true };
    });
    await warmMembers(members, () => true);
    expect(asked().map((p) => Number(/key=(\d+)/.exec(p)![1]))).toEqual([15, 14, 13, 12, 11, 10, 9, 8, 7, 6, 5, 4]);
    expect(most).toBe(1);
  });
  it("stops warming once the conversation is no longer wanted", async () => {
    const { warmMembers } = await load();
    api.get.mockResolvedValue({ prefetched: true });
    let left = 2;
    await warmMembers([1, 2, 3, 4].map((k): Copy => ({ key: k, folder: "INBOX", uid: k })), () => left-- > 0);
    expect(asked()).toHaveLength(2);
  });
});

describe("a list", () => {
  async function listing(source: ListSource | null, grouped = false) {
    const { useMessages } = await load();
    const client = testClient();
    const r = await renderHook((p: { source: ListSource | null }) => useMessages(p.source, grouped), { props: { source }, client });
    return { r, client, settled: () => waitFor(() => expect(r.result.isSuccess || r.result.isError).toBe(true)) };
  }

  it("reads a folder in pages of 200, as conversations when grouped, and says whether it is still syncing", async () => {
    api.get.mockResolvedValue(page([1, 2], { syncing: true }));
    const { r, settled } = await listing({ kind: "folder", account: A, folder: "INBOX" }, true);
    await settled();
    expect(asked()).toEqual([`/api/messages?account=${encodeURIComponent(A)}&folder=INBOX&limit=200&offset=0&group=thread`]);
    const first = r.result.data!.pages[0]!;
    expect(first.rows.map((row) => row.id)).toEqual(["1:10:INBOX", "2:20:INBOX"]);
    expect(first).toMatchObject({ syncing: true, syncFailed: false, catchingUp: false, hasMore: false, total: 2 });
  });
  it("reads the next page from after the rows it holds, and stops when the server says there are no more", async () => {
    api.get.mockResolvedValueOnce(page([1, 2, 3], { has_more: true, total: 5 })).mockResolvedValueOnce(page([4, 5]));
    const { r, settled } = await listing({ kind: "folder", account: A, folder: "Lists" });
    await settled();
    expect(r.result.hasNextPage).toBe(true);
    await r.result.fetchNextPage();
    await waitFor(() => expect(r.result.data?.pages).toHaveLength(2));
    expect(asked()[1]).toContain("offset=3");
    expect(r.result.hasNextPage).toBe(false);
  });
  it("reads a virtual folder over every account without naming one", async () => {
    api.get.mockResolvedValue(page([]));
    const { settled } = await listing({ kind: "across", folder: "__snoozed__" });
    await settled();
    expect(asked()).toEqual(["/api/messages?folder=__snoozed__&limit=200&offset=0"]);
  });
  it("reads every Inbox for All inboxes", async () => {
    api.get.mockResolvedValue(page([1], { syncing: true }));
    const { r, settled } = await listing({ kind: "allInboxes" });
    await settled();
    expect(asked()).toEqual(["/api/inboxes?limit=200&offset=0"]);
    expect(r.result.data!.pages[0]).toMatchObject({ syncing: true, syncFailed: false });
  });
  it("reads nothing for Documents or the Screener, which list on their own, nor with nowhere to be", async () => {
    for (const source of [{ kind: "documents" }, { kind: "folder", account: A, folder: "__screener__" }, null] as (ListSource | null)[]) {
      const { r } = await listing(source);
      expect(r.result.fetchStatus).toBe("idle");
    }
    expect(asked()).toEqual([]);
  });
});

describe("a search", () => {
  async function searching(source: ListSource) {
    const { useMessages } = await load();
    const r = await renderHook(() => useMessages(source, false), { client: testClient() });
    await waitFor(() => expect(r.result.isSuccess || r.result.isError).toBe(true));
    return r;
  }

  it("looks in every account by default", async () => {
    api.get.mockResolvedValue({ items: [item(1, { date: "2026-11-01" })], has_more: false, total: 1 });
    const r = await searching({ kind: "search", q: "from:bo", account: A });
    expect(asked()[0]).toBe(`/api/search?q=from%3Abo&scope=all&account=${encodeURIComponent(A)}&limit=200&offset=0`);
    expect(r.result.data!.pages[0]!.rows[0]).toMatchObject({ id: "1:10:INBOX", date: "2026-11-01" });
  });
  it("looks only in the account you're on when that is what you chose", async () => {
    api.scope = "account";
    api.get.mockResolvedValue({ items: [], has_more: false, total: 0 });
    await searching({ kind: "search", q: "bills", account: A });
    expect(asked()[0]).toContain("scope=account&");
  });
  it("looks everywhere with no account in mind, whatever was chosen", async () => {
    api.scope = "account";
    api.get.mockResolvedValue({ items: [], has_more: false, total: 0 });
    await searching({ kind: "search", q: "bills", account: "" });
    expect(asked()[0]).toContain("scope=all&");
  });
  it("is held to one conversation in its own account", async () => {
    api.get.mockResolvedValue({ items: [], has_more: false, total: 0 });
    await searching({ kind: "search", q: "x", account: A, thread: "t 1" });
    expect(asked()[0]).toContain(`scope=account&account=${encodeURIComponent(A)}&thread=t%201&`);
  });
  it("that the index can't read shows the server's words where its results would be", async () => {
    api.get.mockRejectedValue(new ApiError(400, { detail: "Unbalanced quote in the search" }));
    const r = await searching({ kind: "search", q: '"bills', account: A });
    expect(r.result.data!.pages[0]).toMatchObject({ rows: [], total: 0, hasMore: false, error: "Unbalanced quote in the search" });
  });
  it("that the server failed on is a failure, not an empty list", async () => {
    api.get.mockRejectedValue(new ApiError(502, {}));
    const r = await searching({ kind: "search", q: "bills", account: A });
    expect(r.result.isError).toBe(true);
  });
});

describe("the brief's lists", () => {
  const digest = {
    items: [
      { ...item(1), needs_you: true, importance: 1 }, { ...item(2), needs_you: true, importance: 5 }, { ...item(3), safe_to_file: true }, item(4),
    ],
  };
  const answers = (path: string) => (path === "/api/digest" ? digest : { groups: [{ label: "Travel", messages: [{ key: 4, folder: "INBOX", uid: 40 }, { key: 9, folder: "INBOX", uid: 90 }] }] });

  async function brief(source: ListSource) {
    api.get.mockImplementation(async (path: string) => answers(path));
    const { useMessages } = await load();
    const r = await renderHook(() => useMessages(source, false), { client: testClient() });
    await waitFor(() => expect(r.result.isSuccess).toBe(true));
    return r.result.data!.pages[0]!;
  }

  it("Needs you lists what needs you, the weightiest first", async () => {
    const list = await brief({ kind: "digest", list: "needs" });
    expect(list.rows.map((row) => row.key)).toEqual([2, 1]);
    expect(list).toMatchObject({ total: 2, hasMore: false });
  });
  it("Safe to file lists what is", async () => {
    expect((await brief({ kind: "digest", list: "tidy" })).rows.map((row) => row.key)).toEqual([3]);
  });
  it("a topic lists the brief's mail on it that the digest still holds, and asks the AI nothing", async () => {
    const list = await brief({ kind: "digest", list: "topic", label: "Travel" });
    expect(list.rows.map((row) => row.key)).toEqual([4]);
    expect(asked()).toEqual(["/api/digest", "/api/briefing"]);
  });
});

it("gives each account its folders, and none for one not read yet", async () => {
  const { useEveryAccountsFolders } = await load();
  const B = "bo@example.test";
  api.get.mockImplementation(async (path: string) => {
    if (path.includes(encodeURIComponent(B))) return new Promise(() => {});
    return { folders: [{ name: "INBOX", role: "inbox" }] };
  });
  const r = await renderHook(() => useEveryAccountsFolders([A, B]), { client: testClient() });
  await waitFor(() => expect(r.result.get(A)).toHaveLength(1));
  expect(r.result.get(B)).toEqual([]);
});

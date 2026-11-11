import { act } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ListSource, Route } from "../../app/route";
import { copyId } from "../../lib/copy";
import { cleanup, renderHook, testClient } from "../../lib/testing/renderHook";
import type { ListPage } from "./data";
import type { Row } from "./rows";

const A = "ada@example.test", B = "bo@example.test";

// Where you are, what the server has answered, and what each verb was asked to do.
const the = vi.hoisted(() => ({
  route: { source: null, opened: null } as Route,
  pages: [] as ListPage[],
  digest: { accounts: ["ada@example.test"], aliases: {} } as object | undefined,
  folders: undefined as { folders: { name: string; role: string }[] } | undefined,
  views: [] as { id: string; name: string; q: string }[],
  grouped: true,
  phone: false,
  go: vi.fn(),
  toast: vi.fn(),
  warmBody: vi.fn(),
}));
const verbs = vi.hoisted(() => Object.fromEntries([
  "fileRow", "bulk", "moveRow", "moveMarked", "undo", "setStar", "starRow", "seenRow", "markUnread", "mute", "unmute", "dismissFollowup",
  "replyLater", "replyLaterDone", "snooze", "unsnooze",
].map((name) => [name, vi.fn()])));

vi.mock("../../app/route", async (real) => ({ ...(await real<typeof import("../../app/route")>()), go: the.go, useRoute: () => the.route }));
vi.mock("../../lib/toast", () => ({ toast: the.toast }));
vi.mock("../../lib/prefs", async () => {
  const { createStore } = await import("../../lib/store");
  const conversations = createStore(true);
  return { pref: () => ({ ...conversations, get: () => the.grouped, useValue: () => the.grouped }) };
});
vi.mock("./data", () => ({
  useDigest: () => ({ data: the.digest, isLoadingError: false, refetch: vi.fn() }),
  useMessages: () => ({ data: { pages: the.pages, pageParams: [] }, isSuccess: true, isFetching: false, hasNextPage: false }),
  useFolders: () => ({ data: the.folders }),
  useEveryAccountsFolders: () => new Map(),
  useViews: () => ({ data: { views: the.views } }),
  warmBody: the.warmBody,
}));
vi.mock("./actions", async () => {
  const { createStore } = await import("../../lib/store");
  return { ...verbs, filing: createStore(new Set()), marked: createStore(new Set()), moveAsk: createStore(null), clearMarks: vi.fn() };
});

const row = (key: number, over: Partial<Row> = {}): Row => {
  const r = {
    key, uid: key * 10, account: A, folder: "INBOX", fromName: "Bo", fromAddr: B, subject: `s${key}`, snippet: "", when: "", ts: null, date: "",
    seen: true, flagged: false, hasAttach: false, isJunk: false, replyWorthy: false, bucket: null, threadId: "", count: 1, threadCount: 1,
    action: "", summary: "", needsYou: true, ...over,
  };
  return { ...r, id: copyId(r) };
};
const listing = (...rows: Row[]) => [{ rows, hasMore: false, total: rows.length, syncing: false, syncFailed: false, catchingUp: false }];
const inbox: ListSource = { kind: "folder", account: A, folder: "INBOX" };
const lists: ListSource = { kind: "folder", account: A, folder: "Lists" };
const message = (r: Row) => ({ kind: "message" as const, key: r.key, folder: r.folder, uid: r.uid });
const kept = new Map<string, string>();

let mailbox: typeof import("./useMailbox");
const client = testClient();
const open = () => renderHook(() => mailbox.useMailbox(), { client });
const wentTo = () => the.go.mock.calls.map(([route, how]) => ({ ...(route as Route), ...(how ?? {}) }));

beforeEach(async () => {
  Object.assign(the, { route: { source: inbox, opened: null }, pages: [], digest: { accounts: [A], aliases: {} }, folders: undefined, views: [], grouped: true, phone: false });
  [the.go, the.toast, the.warmBody, ...Object.values(verbs)].forEach((fn) => fn.mockReset());
  kept.clear();
  client.clear();
  vi.stubGlobal("localStorage", { getItem: (k: string) => kept.get(k) ?? null, setItem: (k: string, v: string) => void kept.set(k, v), removeItem: (k: string) => void kept.delete(k) });
  vi.stubGlobal("matchMedia", () => ({ matches: the.phone }));
  vi.resetModules();
  mailbox = await import("./useMailbox");
  const { filing, marked, moveAsk } = await import("./actions");     // a mocked module outlives resetModules
  filing.set(new Set());
  marked.set(new Set());
  moveAsk.set(null);
});
afterEach(async () => {
  await cleanup();
  vi.unstubAllGlobals();
});

describe("the Inbox", () => {
  it("opens on the tab you last chose, Needs you the first time", async () => {
    the.pages = listing(row(1), row(2, { needsYou: false, seen: false }));
    expect((await open()).result.rows.map((r) => r.key)).toEqual([1]);
    await cleanup();
    kept.set("mm_inbox_tab", "unread");
    vi.resetModules();
    mailbox = await import("./useMailbox");
    expect((await open()).result.rows.map((r) => r.key)).toEqual([2]);
  });
  it("keeps the tab you choose for next time", async () => {
    the.pages = listing(row(1), row(2, { needsYou: false }));
    const m = await open();
    await act(async () => mailbox.setInboxTab("all"));
    expect(m.result.rows.map((r) => r.key)).toEqual([1, 2]);
    expect(kept.get("mm_inbox_tab")).toBe("all");
  });
  it("keeps the message you opened in view though the tab would leave it out", async () => {
    const quiet = row(2, { needsYou: false });
    the.pages = listing(row(1), quiet);
    the.route = { source: inbox, opened: message(quiet) };
    expect((await open()).result.rows.map((r) => r.key)).toEqual([1, 2]);
  });
  it("shows a search whole, whatever the tab", async () => {
    the.pages = listing(row(1), row(2, { needsYou: false }));
    the.route = { source: { kind: "search", q: "bo", account: A }, opened: null };
    expect((await open()).result.rows.map((r) => r.key)).toEqual([1, 2]);
  });
  it("shows a copy once though two pages hold it, and none being filed", async () => {
    the.pages = [...listing(row(1), row(2)), ...listing(row(2), row(3))];
    const m = await open();
    expect(m.result.rows.map((r) => r.key)).toEqual([1, 2, 3]);
    const { filing } = await import("./actions");
    await act(async () => filing.set(new Set([row(2).id])));
    expect(m.result.rows.map((r) => r.key)).toEqual([1, 3]);
  });
});

describe("moving through the list", () => {
  it("opens the first row with nothing open, and the next after the one open", async () => {
    the.pages = listing(row(1), row(2), row(3));
    (await open()).result.move(1);
    expect(wentTo()[0]!.opened).toEqual(message(row(1)));
    the.route = { source: inbox, opened: message(row(1)) };
    (await open()).result.move(1);
    expect(wentTo()[1]).toMatchObject({ opened: message(row(2)), replace: true });
  });
  it("goes up from the Inbox's first row to today's brief", async () => {
    the.pages = listing(row(1), row(2));
    the.route = { source: inbox, opened: message(row(1)) };
    (await open()).result.move(-1);
    expect(wentTo()).toEqual([{ source: inbox, opened: null, brief: true }]);
  });
  it("stays on a folder's first row going up, and on its last going down", async () => {
    the.pages = listing(row(1, { folder: "Lists" }), row(2, { folder: "Lists" }));
    the.route = { source: lists, opened: message(row(1, { folder: "Lists" })) };
    const m = (await open()).result;
    the.go.mockReset();
    m.move(-1);
    expect(wentTo()[0]!.opened).toEqual(message(row(1, { folder: "Lists" })));
    the.route = { source: lists, opened: message(row(2, { folder: "Lists" })) };
    (await open()).result.move(5);
    expect(wentTo().at(-1)!.opened).toEqual(message(row(2, { folder: "Lists" })));
  });
  it("opens the next unread either way, and says when there's none", async () => {
    the.pages = listing(row(1, { seen: false }), row(2), row(3), row(4, { seen: false }));
    the.route = { source: { kind: "search", q: "x", account: A }, opened: message(row(2)) };
    const m = (await open()).result;
    expect(m.moveUnread(1)).toBe(true);
    expect(m.moveUnread(-1)).toBe(true);
    expect(wentTo().map((w) => w.opened)).toEqual([message(row(4)), message(row(1))]);
    the.pages = listing(row(1), row(2));
    expect((await open()).result.moveUnread(1)).toBe(false);
  });
  it("opens a conversation whole when grouped, and as its message in a search", async () => {
    const thread = row(1, { threadId: "t1", count: 3 });
    the.pages = listing(thread);
    (await open()).result.open(thread);
    expect(wentTo()[0]!.opened).toEqual({ kind: "thread", id: "t1", account: A, key: 1, folder: "INBOX", uid: 10 });
    the.route = { source: { kind: "search", q: "x", account: A }, opened: null };
    (await open()).result.open(thread);
    expect(wentTo()[1]!.opened).toEqual(message(thread));
  });
  it("warms the next two rows' bodies", async () => {
    the.pages = listing(row(1), row(2), row(3), row(4));
    the.route = { source: inbox, opened: message(row(1)) };
    await open();
    expect(the.warmBody.mock.calls.map(([r]) => (r as Row).key)).toEqual([2, 3]);
  });
});

describe("opening a folder", () => {
  it("opens its first message on a desktop, once", async () => {
    the.pages = listing(row(1, { folder: "Lists" }), row(2, { folder: "Lists" }));
    the.route = { source: lists, opened: null };
    const m = await open();
    expect(wentTo()).toEqual([{ source: lists, opened: message(row(1, { folder: "Lists" })), replace: true }]);
    await m.rerender(undefined);
    expect(the.go).toHaveBeenCalledTimes(1);
  });
  it("stays on the list on a phone, and the Inbox on the brief", async () => {
    the.pages = listing(row(1, { folder: "Lists" }));
    the.phone = true;
    the.route = { source: lists, opened: null };
    await open();
    the.phone = false;
    the.pages = listing(row(1));
    the.route = { source: inbox, opened: null };
    await open();
    expect(the.go).not.toHaveBeenCalled();
  });
  it("moves on from a draft that was saved again or discarded and is no longer in the list", async () => {
    the.folders = { folders: [{ name: "Drafts", role: "drafts" }] };
    const next = row(5, { folder: "Drafts" });
    the.pages = listing(next);
    the.route = { source: { kind: "folder", account: A, folder: "Drafts" }, opened: { kind: "message", key: 4, folder: "Drafts", uid: 40 } };
    await open();
    expect(wentTo()).toEqual([{ source: the.route.source, opened: message(next), replace: true }]);
  });
});

describe("filing what you are reading", () => {
  it("goes on to the next row, or the one before at the end", async () => {
    the.pages = listing(row(1), row(2), row(3));
    the.route = { source: inbox, opened: message(row(2)) };
    (await open()).result.fileOpen("archive");
    expect(verbs.fileRow).toHaveBeenCalledWith(client, "archive", { key: 2, folder: "INBOX", uid: 20 }, false, { source: inbox, opened: message(row(3)) }, undefined);
    the.route = { source: inbox, opened: message(row(3)) };
    (await open()).result.fileOpen("trash");
    expect(verbs.fileRow!.mock.calls[1]![4]).toEqual({ source: inbox, opened: message(row(2)) });
  });
  it("files a conversation open whole as a whole", async () => {
    const thread = row(1, { threadId: "t1", count: 2 });
    the.pages = listing(thread);
    the.route = { source: inbox, opened: { kind: "thread", id: "t1", account: A, key: 1, folder: "INBOX", uid: 10 } };
    (await open()).result.fileOpen("archive");
    expect(verbs.fileRow!.mock.calls[0]![3]).toBe(true);
  });
  it("leaves you where you are filing a row you aren't reading", async () => {
    the.pages = listing(row(1), row(2));
    the.route = { source: inbox, opened: message(row(1)) };
    (await open()).result.file(row(2), "archive");
    expect(verbs.fileRow!.mock.calls[0]![4]).toBeNull();
  });
  it("files the marked rows, going on past them when what you read is among them", async () => {
    the.pages = listing(row(1), row(2), row(3), row(4));
    the.route = { source: inbox, opened: message(row(2)) };
    const { marked } = await import("./actions");
    marked.set(new Set([row(2).id, row(3).id]));
    (await open()).result.bulk("trash");
    const [, verb, rows, , after] = verbs.bulk!.mock.calls[0]!;
    expect([verb, (rows as Row[]).map((r) => r.key), after]).toEqual(["trash", [2, 3], { source: inbox, opened: message(row(4)) }]);
  });
});

describe("moving to a folder", () => {
  it("won't move marked mail from two accounts together", async () => {
    the.pages = listing(row(1), row(2, { account: B }));
    const { marked, moveAsk } = await import("./actions");
    marked.set(new Set([row(1).id, row(2, { account: B }).id]));
    (await open()).result.askMove();
    expect(the.toast).toHaveBeenCalledWith("Those are in different accounts — mark one account’s mail to move it");
    expect(moveAsk.get()).toBeNull();
  });
  it("offers one account's folders, from the folder marked mail shares", async () => {
    the.pages = listing(row(1), row(2), row(3, { folder: "Lists" }));
    const { marked, moveAsk } = await import("./actions");
    marked.set(new Set([row(1).id, row(2).id]));
    (await open()).result.askMove();
    expect(moveAsk.get()).toEqual({ account: A, from: "INBOX" });
    marked.set(new Set([row(1).id, row(3, { folder: "Lists" }).id]));
    (await open()).result.askMove();
    expect(moveAsk.get()).toEqual({ account: A, from: null });
  });
  it("moves only the marked mail not already there", async () => {
    the.pages = listing(row(1), row(2, { folder: "Lists" }));
    const { marked } = await import("./actions");
    marked.set(new Set([row(1).id, row(2, { folder: "Lists" }).id]));
    (await open()).result.moveTo("Lists", "Lists");
    expect((verbs.moveMarked!.mock.calls[0]![3] as Row[]).map((r) => r.key)).toEqual([1]);
  });
});

describe("replying", () => {
  it("answers the message open", async () => {
    the.pages = listing(row(1));
    the.route = { source: inbox, opened: message(row(1)) };
    expect((await open()).result.replyTo()).toEqual({ key: 1, folder: "INBOX", uid: 10 });
  });
  it("answers a conversation's latest message that isn't yours, as the thread holds it", async () => {
    the.route = { source: inbox, opened: { kind: "thread", id: "t1", account: A, key: 3, folder: "Sent", uid: 30 } };
    const m = await open();
    expect(m.result.replyTo()).toBeNull();
    client.setQueryData(["thread", A, "t1"], { items: [{ key: 2, folder: "INBOX", uid: 20 }, { key: 3, folder: "Sent", uid: 30 }], reply_key: 2 });
    expect(m.result.replyTo()).toEqual({ key: 2, folder: "INBOX", uid: 20 });
  });
  it("names a conversation whole for what is done to it, and a message by its copy", async () => {
    the.route = { source: inbox, opened: { kind: "thread", id: "t1", account: A, key: 3, folder: "INBOX", uid: 30 } };
    expect((await open()).result.target()).toEqual({ account: A, thread_id: "t1" });
    the.route = { source: inbox, opened: message(row(1)) };
    expect((await open()).result.target()).toEqual({ key: 1, folder: "INBOX", uid: 10 });
  });
});

describe("where you are", () => {
  it("is titled by what the list is", async () => {
    const title = async (source: ListSource, folders?: typeof the.folders) => {
      the.route = { source, opened: null };
      the.folders = folders;
      return (await open()).result.title;
    };
    expect(await title({ kind: "allInboxes" })).toBe("Inbox");
    expect(await title({ kind: "search", q: "x", account: A, lane: "papertrail" })).toBe("Paper Trail");
    expect(await title({ kind: "search", q: "x", account: A })).toBe("Search");
    expect(await title({ kind: "digest", list: "needs" })).toBe("Needs you");
    expect(await title({ kind: "digest", list: "topic", label: "Travel" })).toBe("Travel");
    expect(await title({ kind: "across", folder: "__waiting__" })).toBe("Waiting");
    expect(await title({ kind: "folder", account: A, folder: "[Gmail]/All Mail" }, { folders: [{ name: "[Gmail]/All Mail", role: "all" }] })).toBe("All mail");
  });
  it("is on the folder's account, or the first account with none named", async () => {
    the.digest = { accounts: [B, A], aliases: {} };
    the.route = { source: inbox, opened: null };
    expect((await open()).result.account).toBe(A);
    the.route = { source: { kind: "search", q: "x", account: "" }, opened: null };
    expect((await open()).result.account).toBe(B);
    the.route = { source: { kind: "allInboxes" }, opened: null };
    expect((await open()).result.account).toBeUndefined();
  });
  it("goes to a virtual folder across every account from All inboxes, and to this account's own folder otherwise", async () => {
    the.route = { source: { kind: "allInboxes" }, opened: null };
    (await open()).result.goToFolder("__snoozed__");
    the.route = { source: inbox, opened: null };
    (await open()).result.goToFolder("__snoozed__");
    expect(wentTo().map((w) => w.source)).toEqual([{ kind: "across", folder: "__snoozed__" }, { kind: "folder", account: A, folder: "__snoozed__" }]);
  });
  it("searches the account it is on", async () => {
    the.route = { source: inbox, opened: null };
    (await open()).result.search("from:bo");
    expect(wentTo()[0]!.source).toEqual({ kind: "search", q: "from:bo", account: A });
  });
});

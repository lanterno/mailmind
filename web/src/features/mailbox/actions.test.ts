import type { QueryClient } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { get, post } from "../../lib/api/client";
import { LIMITS } from "../../lib/api/limits.gen";
import { copyOf, type Copy } from "../../lib/copy";
import { toast } from "../../lib/toast";
import { armDwell, bulk, fileRow, filing, markOpenedRead, moveMarked, moveRow, readOnly, seenRow, snooze, starRow, undo } from "./actions";
import type { Row } from "./rows";

vi.mock("../../lib/api/client", () => ({ get: vi.fn(), post: vi.fn(), refusalOf: vi.fn() }));
vi.mock("../../lib/toast", () => ({ toast: vi.fn(), OFFERS_MS: 8000, createFloat: () => ({ show: vi.fn(), clear: vi.fn() }) }));

const aPageLoad = async () => {
  vi.resetModules();
  return (await import("./actions")).undoGroup;
};

it("gives each undo group an id of its own across page loads, since the undo stack outlives a page", async () => {
  const first = await aPageLoad(), second = await aPageLoad();
  const ids = [first(), first(), second(), second()];
  expect(new Set(ids).size).toBe(4);
  for (const id of ids) expect(id).toMatch(/^ug-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/);
});

describe("snoozing marked rows", () => {
  const qc = { setQueriesData: vi.fn(), setQueryData: vi.fn(), invalidateQueries: vi.fn() } as unknown as QueryClient;
  const marked = (n: number): Copy[] => Array.from({ length: n }, (_, i) => ({ key: i + 1, folder: "INBOX", uid: 1000 + i }));
  const sent = () => vi.mocked(post).mock.calls.map(([path, body]) => ({ path, ...(body as { copies: Copy[]; preset: string; undo_group: string }) }));
  const snoozing = (copies: Copy[]) => snooze(qc, { copies }, { preset: "tomorrow" }, copies.map((c) => `${c.key}:${c.uid}:${c.folder}`), null);

  beforeEach(() => {
    vi.mocked(post).mockReset().mockResolvedValue({ ok: true, count: 1, until: 1 });
    vi.mocked(toast).mockReset();
    vi.stubGlobal("location", { pathname: "/", search: "" });
  });
  afterEach(() => vi.unstubAllGlobals());

  it("sends 1,200 as three requests of at most the server's limit, under one undo group", async () => {
    const copies = marked(1200);
    await snoozing(copies);
    const asks = sent();
    expect(LIMITS.snooze_copies).toBe(500);
    expect(asks.map((a) => [a.path, a.copies.length, a.preset])).toEqual([
      ["/api/snooze", 500, "tomorrow"], ["/api/snooze", 500, "tomorrow"], ["/api/snooze", 200, "tomorrow"]]);
    expect(asks.flatMap((a) => a.copies)).toEqual(copies);
    expect(new Set(asks.map((a) => a.undo_group)).size).toBe(1);
    expect(asks[0]!.undo_group).toMatch(/^ug-/);
    expect(toast).toHaveBeenCalledWith("Snoozed");
  });

  it("sends as many as one request may name as one request", async () => {
    await snoozing(marked(LIMITS.snooze_copies));
    expect(sent().map((a) => a.copies.length)).toEqual([LIMITS.snooze_copies]);
  });

  it("says how many failed when a request fails, and still sends the rest", async () => {
    vi.mocked(post).mockResolvedValueOnce({ ok: true, count: 500, until: 1 }).mockRejectedValueOnce(new Error("HTTP 502"));
    await snoozing(marked(1200));
    expect(sent()).toHaveLength(3);
    expect(toast).toHaveBeenCalledWith("Snoozed 700, 500 failed");
  });

  it("says why when none went, as one request would", async () => {
    vi.mocked(post).mockRejectedValue(new Error("bad snooze time"));
    await snoozing(marked(1200));
    expect(sent()).toHaveLength(3);
    expect(toast).toHaveBeenCalledWith("Couldn’t snooze: bad snooze time");
  });
});

describe("moving to a folder someone picked", () => {
  const qc = { setQueriesData: vi.fn(), setQueryData: vi.fn(), invalidateQueries: vi.fn() } as unknown as QueryClient;
  const marked = (n: number) => Array.from({ length: n }, (_, i) => ({ id: `${i + 1}:${1000 + i}:INBOX`, key: i + 1, folder: "INBOX", uid: 1000 + i, threadId: null })) as unknown as Row[];
  const sent = () => vi.mocked(post).mock.calls.map(([path, body]) => ({ path, ...(body as { to: string; rows: unknown[]; undo_group: string }) }));

  beforeEach(() => {
    vi.mocked(post).mockReset().mockResolvedValue({ ok: true, count: 1, undoable: true, rows_failed: 0, partial: false });
    vi.mocked(toast).mockReset();
    vi.stubGlobal("location", { pathname: "/", search: "" });
  });
  afterEach(() => vi.unstubAllGlobals());

  const one: Copy = { key: 7, folder: "INBOX", uid: 70 };

  it("sends one message with the folder, and says where it went", async () => {
    await moveRow(qc, "Clients/Acme", "Acme", one, false, null);
    expect(vi.mocked(post).mock.calls).toEqual([["/api/move", { ...one, to: "Clients/Acme" }]]);
    expect(toast).toHaveBeenCalledWith("Moved to Acme");
  });

  it("sends a conversation to its own route", async () => {
    await moveRow(qc, "Clients", "Clients", one, true, null);
    expect(vi.mocked(post).mock.calls.map(([path]) => path)).toEqual(["/api/thread/move"]);
  });

  it("says why when the server won't", async () => {
    vi.mocked(post).mockRejectedValueOnce(new Error("no such folder on this account"));
    await moveRow(qc, "Gone", "Gone", one, false, null);
    expect(toast).toHaveBeenCalledWith("Couldn’t move: no such folder on this account");
  });

  it("sends more marked rows than one request may name as more requests, under one undo group", async () => {
    const rows = marked(LIMITS.bulk_rows + 3);
    await moveMarked(qc, "Clients", "Clients", rows, () => false, null);
    const asks = sent();
    expect(asks.map((a) => [a.path, a.to, a.rows.length])).toEqual([["/api/bulk/move", "Clients", LIMITS.bulk_rows], ["/api/bulk/move", "Clients", 3]]);
    expect(new Set(asks.map((a) => a.undo_group)).size).toBe(1);
    expect(toast).toHaveBeenCalledWith(`Moved ${rows.length} to Clients`);
  });

  it("names each row's conversation only where the row stands for one", async () => {
    const rows = marked(2);
    await moveMarked(qc, "Clients", "Clients", rows, (r) => r === rows[1], null);
    expect(sent()[0]!.rows).toEqual([{ ...copyOf(rows[0]!), whole: false }, { ...copyOf(rows[1]!), whole: true }]);
  });

  it("counts every row of a request that failed, and still sends the rest", async () => {
    vi.mocked(post).mockRejectedValueOnce(new Error("HTTP 502"));
    await moveMarked(qc, "Clients", "Clients", marked(LIMITS.bulk_rows + 3), () => false, null);
    expect(sent()).toHaveLength(2);
    expect(toast).toHaveBeenCalledWith("Moved 3 to Clients, " + LIMITS.bulk_rows + " failed");
  });
});

describe("a conversation row's star and read buttons", () => {
  const qc = { setQueriesData: vi.fn(), setQueryData: vi.fn(), invalidateQueries: vi.fn() } as unknown as QueryClient;
  const member = (key: number, seen: boolean, flagged: boolean) => ({ key, folder: "INBOX", uid: 100 + key, seen, flagged });
  const row = (over: Partial<Row>): Row => ({ id: "1:101:INBOX", key: 1, folder: "INBOX", uid: 101, account: "ada@example.test", threadId: "t1", seen: false, flagged: false, count: 3, ...over }) as Row;
  const asked = () => vi.mocked(post).mock.calls.map(([path, body]) => [path, (body as Copy).key, (body as { seen?: boolean; on?: boolean }).seen ?? (body as { on?: boolean }).on]);

  beforeEach(() => {
    vi.mocked(post).mockReset().mockResolvedValue({ ok: true });
    vi.mocked(get).mockReset().mockResolvedValue({ ok: true, items: [member(1, false, false), member(2, true, true), member(3, false, true)] } as never);
    vi.mocked(toast).mockReset();
    vi.mocked(qc.setQueriesData).mockReset();
  });

  it("marks read every member that is unread, as the row reads read only when none is", async () => {
    await seenRow(qc, row({ seen: false }), true, false);
    expect(asked()).toEqual([["/api/seen", 1, true], ["/api/seen", 3, true]]);
    expect(toast).toHaveBeenCalledWith("Marked read");
  });

  it("marks unread only the copy the row names, which is enough for it to read unread", async () => {
    await seenRow(qc, row({ seen: true }), true, false);
    expect(get).not.toHaveBeenCalled();
    expect(asked()).toEqual([["/api/seen", 1, false]]);
  });

  it("unstars every member that is starred, as the row reads starred while any is", async () => {
    await starRow(qc, row({ flagged: true }), true);
    expect(asked()).toEqual([["/api/flag", 2, false], ["/api/flag", 3, false]]);
    expect(toast).toHaveBeenCalledWith("Unstarred");
  });

  it("stars only the copy the row names", async () => {
    await starRow(qc, row({ flagged: false }), true);
    expect(get).not.toHaveBeenCalled();
    expect(asked()).toEqual([["/api/flag", 1, true]]);
  });

  it("asks the server for no members of a row that is one message", async () => {
    await seenRow(qc, row({ seen: false }), false, false);
    expect(get).not.toHaveBeenCalled();
    expect(asked()).toEqual([["/api/seen", 1, true]]);
  });

  it("puts the row back and says so when the server says no", async () => {
    vi.mocked(post).mockRejectedValue(new Error("HTTP 500"));
    await seenRow(qc, row({ seen: false }), true, false);
    expect(vi.mocked(qc.setQueriesData)).toHaveBeenCalledTimes(2);
    expect(toast).toHaveBeenCalledWith("Couldn’t update");
  });
});

describe("a change read only refuses", () => {
  const qc = { setQueriesData: vi.fn(), setQueryData: vi.fn(), invalidateQueries: vi.fn(), removeQueries: vi.fn() } as unknown as QueryClient;
  const WORDS = "Read only is on, so nothing was changed. Turn it off in Settings › General.";
  // The server's 409, as the client throws it: what the page reads is `readOnly`.
  const refused = () => Object.assign(new Error("MailMind is running read-only"), { status: 409, readOnly: true });
  const rows = (n: number) => Array.from({ length: n }, (_, i) => ({ id: `${i + 1}:${1000 + i}:INBOX`, key: i + 1, folder: "INBOX", uid: 1000 + i, account: "ada@example.test", threadId: `t${i + 1}`, seen: false, flagged: false })) as unknown as Row[];
  const one: Copy = { key: 7, folder: "INBOX", uid: 70 };

  beforeEach(() => {
    vi.mocked(post).mockReset().mockRejectedValue(refused());
    vi.mocked(get).mockReset();
    vi.mocked(toast).mockReset();
    vi.mocked(qc.setQueriesData).mockReset();
    vi.stubGlobal("location", { pathname: "/", search: "" });
  });
  afterEach(() => vi.unstubAllGlobals());

  it("says so in its own words when filing is refused, and the row comes back", async () => {
    await fileRow(qc, "archive", one, false, null);
    expect(toast).toHaveBeenCalledWith(WORDS);
    expect(filing.get().size).toBe(0);
  });

  it("says so for a move, not the server's words", async () => {
    await moveRow(qc, "Clients", "Clients", one, false, null);
    expect(toast).toHaveBeenCalledWith(WORDS);
    expect(toast).not.toHaveBeenCalledWith(expect.stringContaining("Couldn’t move"));
  });

  it("asks once for marked rows, since it refuses every request alike, and says so rather than counting them failed", async () => {
    await moveMarked(qc, "Clients", "Clients", rows(LIMITS.bulk_rows + 3), () => false, null);
    expect(vi.mocked(post).mock.calls).toHaveLength(1);
    expect(toast).toHaveBeenCalledWith(WORDS);
    await bulk(qc, "archive", rows(3), () => false, null);
    expect(toast).toHaveBeenLastCalledWith(WORDS);
  });

  it("stops at the first star or read mark of marked rows, and says so", async () => {
    await bulk(qc, "star", rows(3), () => false, null);
    expect(vi.mocked(post).mock.calls).toHaveLength(1);
    expect(toast).toHaveBeenCalledWith(WORDS);
    expect(toast).not.toHaveBeenCalledWith(expect.stringMatching(/^Updated/));
  });

  it("says so for muting marked conversations", async () => {
    await bulk(qc, "mute", rows(2), () => true, null);
    expect(vi.mocked(post).mock.calls).toHaveLength(1);
    expect(toast).toHaveBeenCalledWith(WORDS);
  });

  it("puts a row's mark back and says so", async () => {
    await seenRow(qc, { ...rows(1)[0]!, seen: false }, false, false);
    expect(vi.mocked(qc.setQueriesData)).toHaveBeenCalledTimes(2);
    expect(toast).toHaveBeenCalledWith(WORDS);
    await starRow(qc, rows(1)[0]!, false);
    expect(toast).toHaveBeenLastCalledWith(WORDS);
  });

  it("says so for a snooze", async () => {
    await snooze(qc, { key: 7, folder: "INBOX", uid: 70 }, { preset: "tomorrow" }, ["7:70:INBOX"], null);
    expect(toast).toHaveBeenCalledWith(WORDS);
  });

  it("says so for an Undo that would reach a server, which stays to undo", async () => {
    await undo(qc);
    expect(toast).toHaveBeenCalledWith(WORDS);
    expect(qc.removeQueries).not.toHaveBeenCalled();
  });
});

describe("opening a message", () => {
  const qc = { setQueriesData: vi.fn(), setQueryData: vi.fn(), invalidateQueries: vi.fn() } as unknown as QueryClient;
  const one: Copy = { key: 7, folder: "INBOX", uid: 70 };

  beforeEach(() => {
    vi.useFakeTimers();
    vi.mocked(post).mockReset().mockResolvedValue({ ok: true });
    vi.mocked(qc.setQueriesData).mockReset();
    readOnly.set(false);
  });
  afterEach(() => {
    readOnly.set(false);
    vi.useRealTimers();
  });

  it("marks it read after a moment on it", async () => {
    armDwell(qc, one);
    await vi.advanceTimersByTimeAsync(1200);
    expect(vi.mocked(post).mock.calls).toEqual([["/api/seen", { ...one, seen: true }]]);
  });

  it("asks no server to mark it read while read only is on, which would refuse it, and leaves its row as it was", async () => {
    readOnly.set(true);
    armDwell(qc, one);
    await vi.advanceTimersByTimeAsync(5000);
    markOpenedRead(qc, one);              // a conversation's member, opened
    expect(post).not.toHaveBeenCalled();
    expect(qc.setQueriesData).not.toHaveBeenCalled();
  });
});

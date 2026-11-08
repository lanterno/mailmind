import type { QueryClient } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { get, post } from "../../lib/api/client";
import { LIMITS } from "../../lib/api/limits.gen";
import type { Copy } from "../../lib/copy";
import { toast } from "../../lib/toast";
import { seenRow, snooze, starRow } from "./actions";
import type { Row } from "./rows";

vi.mock("../../lib/api/client", () => ({ get: vi.fn(), post: vi.fn(), refusalOf: vi.fn() }));
vi.mock("../../lib/toast", () => ({ toast: vi.fn() }));

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

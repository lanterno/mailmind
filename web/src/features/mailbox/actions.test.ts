import type { QueryClient } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { post } from "../../lib/api/client";
import { LIMITS } from "../../lib/api/limits.gen";
import type { Copy } from "../../lib/copy";
import { toast } from "../../lib/toast";
import { snooze } from "./actions";

vi.mock("../../lib/api/client", () => ({ post: vi.fn(), refusalOf: vi.fn() }));
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

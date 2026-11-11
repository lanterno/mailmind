import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Route } from "../../app/route";

const client = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));
vi.mock("../../lib/api/client", () => ({ get: client.get, post: client.post }));
vi.mock("../../lib/prefs", () => ({ mayNotify: () => false }));
vi.mock("../../app/route", () => ({ go: vi.fn() }));

const ADA = "ada@example.test", BEA = "bea@example.test";
const T = Date.UTC(2026, 10, 2, 9, 0);
const inbox = (account: string): Route => ({ source: { kind: "folder", account, folder: "INBOX" }, opened: null });
const elsewhere: Route = { source: { kind: "documents" }, opened: null };

let kept: Map<string, string>;
const load = async () => {
  vi.resetModules();
  return import("./NewMail");
};
const since = () => client.get.mock.calls.map((call) => new URLSearchParams(String(call[0]).split("?")[1]).get("since"));

beforeEach(() => {
  vi.useFakeTimers({ now: T });
  vi.resetAllMocks();
  client.get.mockResolvedValue({ empty: true });
  kept = new Map();
  vi.stubGlobal("localStorage", {
    getItem: (key: string) => kept.get(key) ?? null,
    setItem: (key: string, value: string) => void kept.set(key, value),
    removeItem: (key: string) => void kept.delete(key),
  });
  vi.stubGlobal("document", { hidden: false, title: "" });
});
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("when an Inbox was last looked at", () => {
  it("is kept by the account's place in the list, so no address is", async () => {
    const { lookedAt } = await load();
    lookedAt(inbox(BEA).source, [ADA, BEA]);
    expect(JSON.parse(kept.get("mm_looked")!)).toEqual({ 1: T });
    vi.setSystemTime(T + 60_000);
    lookedAt({ kind: "allInboxes" }, [ADA, BEA]);
    expect(JSON.parse(kept.get("mm_looked")!)).toEqual({ 0: T + 60_000, 1: T + 60_000 });
    expect([...kept].join(" ")).not.toMatch(/@|ada|bea/);
  });
  it("lets go of what this browser kept of it by address", async () => {
    kept.set("mm_lastlook", JSON.stringify({ [ADA]: T - 1000 }));
    const { lookedAt } = await load();
    lookedAt(inbox(ADA).source, [ADA]);
    expect(kept.has("mm_lastlook")).toBe(false);
    expect([...kept].join(" ")).not.toContain("@");
  });
  it("is what the line on new mail starts from, read by the account's place", async () => {
    kept.set("mm_looked", JSON.stringify({ 1: T }));
    const { onNewMail } = await load();
    vi.setSystemTime(T + 3_600_000);
    onNewMail({ account: BEA, count: 2 }, inbox(ADA), true, (a) => a, [ADA, BEA]);
    await vi.advanceTimersByTimeAsync(1500);
    expect(since()).toEqual([String(T / 1000)]);
  });
  it("is six hours back for an account never looked at, whatever was once kept by its address, or one the page doesn't list", async () => {
    kept.set("mm_looked", JSON.stringify({ 0: T }));
    kept.set("mm_lastlook", JSON.stringify({ [BEA]: T - 1000 }));
    const first = await load();
    first.onNewMail({ account: BEA }, elsewhere, true, (a) => a, [ADA, BEA]);
    await vi.advanceTimersByTimeAsync(1500);
    expect(kept.has("mm_lastlook")).toBe(false);
    const second = await load();
    second.onNewMail({ account: "cy@example.test" }, elsewhere, true, (a) => a, [ADA, BEA]);
    await vi.advanceTimersByTimeAsync(1500);
    expect(since()).toEqual([String((T - 6 * 3_600_000) / 1000), String(Math.floor((T - 6 * 3_600_000 + 1500) / 1000))]);
  });
  it("is what mail back from a snooze starts from, too", async () => {
    kept.set("mm_looked", JSON.stringify({ 1: T }));
    const { onSnoozeWake, onNewMail } = await load();
    vi.setSystemTime(T + 3_600_000);
    onSnoozeWake({ accounts: [BEA], count: 1 }, elsewhere, [ADA, BEA]);
    onNewMail({ account: BEA }, elsewhere, true, (a) => a, [ADA, BEA]);
    await vi.advanceTimersByTimeAsync(1500);
    expect(since()).toEqual([String(T / 1000)]);
  });
});

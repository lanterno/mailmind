import { describe, expect, it } from "vitest";
import { pillLabel } from "./model";

const digest = (over: object = {}) => ({ accounts: ["a@example.test"], aliases: {}, status: {}, sync: { last: null }, profile: "full", ...over }) as never;
const now = new Date(2026, 10, 2, 12, 0);

describe("what the pill says", () => {
  it("says it's connecting before the first digest", () => expect(pillLabel(undefined, false, false, now).text).toBe("Connecting…"));
  it("puts an account that can't connect first", () => {
    const one = digest({ status: { "a@example.test": { health: "error" } }, aliases: { "a@example.test": "Ada" } });
    expect(pillLabel(one, true, true, now)).toEqual({ cls: "err", text: "Ada can’t connect" });
    const two = digest({ status: { "a@example.test": { health: "error" }, "b@example.test": { health: "error" } } });
    expect(pillLabel(two, false, false, now).text).toBe("2 accounts can’t connect");
  });
  it("says what's running", () => {
    expect(pillLabel(digest(), false, true, now).text).toBe("Reconciling…");
    expect(pillLabel(digest(), true, false, now).text).toBe("Syncing…");
    expect(pillLabel(digest({ sync: { last: null, running: true } }), false, false, now).text).toBe("Syncing…");
    expect(pillLabel(digest({ status: { "a@example.test": { health: "syncing" } } }), false, false, now).text).toBe("Syncing…");
  });
  it("says read-only rather than when it last synced", () => {
    expect(pillLabel(digest({ profile: "read-only", sync: { last: now.toISOString() } }), false, false, now).text).toBe("Read-only");
  });
  it("says when it last synced, by the time today and by the date before", () => {
    const today = pillLabel(digest({ sync: { last: new Date(2026, 10, 2, 9, 5).toISOString() } }), false, false, now);
    expect(today.cls).toBe("ok");
    expect(today.text).toMatch(/^Synced 9:05/);
    expect(pillLabel(digest({ sync: { last: new Date(2026, 9, 30).toISOString() } }), false, false, now).text).toMatch(/^Synced Oct 30/);
    expect(pillLabel(digest(), false, false, now).text).toBe("Not synced yet");
  });
});

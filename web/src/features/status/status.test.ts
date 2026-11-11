import { describe, expect, it } from "vitest";
import { coachPlace, guideOffers, guideSteps } from "./guideSteps";
import { badAccounts, nameOf, pillLabel } from "./model";

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

describe("each account", () => {
  it("is named by its own name, else by the address before the @", () => {
    const named = digest({ aliases: { "a@example.test": "Ada" } });
    expect(nameOf(named, "a@example.test")).toBe("Ada");
    expect(nameOf(named, "bo@example.test")).toBe("bo");
    expect(nameOf(undefined, "bo@example.test")).toBe("bo");
  });
  it("is down for the rail only when it can’t connect, not while it syncs", () => {
    const status = { "a@example.test": { health: "error" }, "b@example.test": { health: "syncing" }, "c@example.test": { health: "ok" } };
    expect(badAccounts(digest({ status }))).toEqual(["a@example.test"]);
    expect(badAccounts(undefined)).toEqual([]);
  });
});

describe("the first-run guide", () => {
  const gate = { prefsRead: true, offered: false, accounts: 1, inboxHasMail: true, busy: false };
  const ids = (ai: boolean, singleKeys = true) => guideSteps({ ai, singleKeys }).map((s) => s.id);

  it("is offered once, with mail and nothing in the way", () => {
    expect(guideOffers(gate)).toBe(true);
    expect(guideOffers({ ...gate, offered: true })).toBe(false);
    expect(guideOffers({ ...gate, accounts: 0 })).toBe(false);
    expect(guideOffers({ ...gate, inboxHasMail: false })).toBe(false);
    expect(guideOffers({ ...gate, busy: true })).toBe(false);
  });
  it("isn't offered on a guess: only once the server has answered", () => expect(guideOffers({ ...gate, prefsRead: false })).toBe(false));
  it("covers what it should, and drops what needs an AI that's off", () => {
    expect(ids(true)).toEqual(["brief", "rail", "palette", "ask", "screener"]);
    expect(ids(false)).toEqual(["brief", "rail", "palette", "screener"]);
  });
  it("doesn't teach a key that is switched off", () => {
    const rail = (singleKeys: boolean) => guideSteps({ ai: false, singleKeys }).find((s) => s.id === "rail")!.text;
    expect(rail(true)).toContain("↵");
    expect(rail(false)).not.toContain("↵");
    expect(rail(false)).toContain("Focus reading");
  });
});

describe("where a coach-mark sits", () => {
  const view = { width: 1000, height: 800 }, card = { width: 300, height: 200 };

  it("centres with nothing to point at", () => expect(coachPlace(null, view, card)).toEqual({ top: 300, left: 350 }));
  it("goes beside what it points at", () => expect(coachPlace({ top: 100, left: 20, width: 200, height: 400 }, view, card)).toEqual({ top: 100, left: 232 }));
  it("goes to the other side when there's no room", () => expect(coachPlace({ top: 100, left: 690, width: 300, height: 400 }, view, card)).toEqual({ top: 100, left: 378 }));
  it("goes under, then over, a target as wide as the window", () => {
    expect(coachPlace({ top: 40, left: 0, width: 1000, height: 100 }, view, card)).toEqual({ top: 152, left: 12 });
    expect(coachPlace({ top: 600, left: 0, width: 1000, height: 190 }, view, card)).toEqual({ top: 388, left: 12 });
  });
  it("stays inside the window", () => {
    const { top, left } = coachPlace({ top: 780, left: 900, width: 90, height: 10 }, view, card);
    expect(top).toBeLessThanOrEqual(view.height - card.height);
    expect(left).toBeLessThanOrEqual(view.width - card.width);
    expect(top).toBeGreaterThan(0);
    expect(left).toBeGreaterThan(0);
  });
});

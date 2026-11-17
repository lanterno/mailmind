import { describe, expect, it } from "vitest";
import { draftWhy, heldWhy } from "./tray";

const draft = { kept: false, discarding: false, attempts: 0 };
const held = { state: "queued", error: null, attempts: 0 };

describe("the tray's drafts", () => {
  it("says a draft saved read-only is kept on this Mac, read only on or off, since only a save sends it", () => {
    expect(draftWhy({ ...draft, kept: true }, true)).toBe("Kept on this Mac");
    expect(draftWhy({ ...draft, kept: true }, false)).toBe("Kept on this Mac");
    expect(draftWhy({ ...draft, kept: true, attempts: 2 }, false)).toBe("Kept on this Mac");
  });
  it("says one a full run queued earlier waits for the next, read only", () => {
    expect(draftWhy(draft, true)).toBe("reaches the server on a full run");
    expect(draftWhy({ ...draft, discarding: true }, true)).toBe("leaves the server on a full run");
  });
  it("says one on its way goes once its server is back, or how often it was tried", () => {
    expect(draftWhy(draft, false)).toBe("reaches the server once it’s back");
    expect(draftWhy({ ...draft, attempts: 3 }, false)).toBe("tried 3×, trying again");
  });
});

describe("the tray's changes waiting", () => {
  it("says what a full run queued earlier waits for the next, read only", () => {
    expect(heldWhy(held, true)).toBe("waits for a full run");
  });
  it("says when one goes, or why it is stuck", () => {
    expect(heldWhy(held, false)).toBe("once the server is back");
    expect(heldWhy({ ...held, attempts: 2 }, false)).toBe("tried 2×, trying again");
    expect(heldWhy({ ...held, state: "error", error: "refused" }, true)).toBe("the server refused it");
    expect(heldWhy({ ...held, state: "error", error: "something else" }, false)).toBe("couldn’t reach the server");
  });
});

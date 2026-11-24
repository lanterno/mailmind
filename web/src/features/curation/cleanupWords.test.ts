import { describe, expect, it } from "vitest";
import { sendsUnsubscribe, wayOutChip } from "./cleanupWords";

describe("whether a cleanup sends an unsubscribe, which can't be undone", () => {
  it("sends one by one click or an email when the box is ticked", () => {
    expect(sendsUnsubscribe("one-click", true)).toBe(true);
    expect(sendsUnsubscribe("mailto", true)).toBe(true);
  });

  it("sends none with the box unticked, a page that only opens, or no way to unsubscribe", () => {
    expect(sendsUnsubscribe("one-click", false)).toBe(false);
    expect(sendsUnsubscribe("mailto", false)).toBe(false);
    expect(sendsUnsubscribe("http-page", true)).toBe(false);
    expect(sendsUnsubscribe("none", false)).toBe(false);
  });
});

describe("what a sender's row says of its way out", () => {
  it("says how, as its newest mail stored names it", () => {
    expect(wayOutChip("one-click", true)).toBe("1-click");
    expect(wayOutChip("mailto", true)).toBe("by email");
    expect(wayOutChip("http-page", false)).toBe("web page");
  });

  it("says nothing where the list offers none", () => {
    expect(wayOutChip("none", true)).toBeNull();
  });

  it("says only newsletter where no header was read, never one click", () => {
    expect(wayOutChip(null, true)).toBe("newsletter");
    expect(wayOutChip(null, false)).toBeNull();
    expect(wayOutChip(undefined, false)).toBeNull();
  });
});

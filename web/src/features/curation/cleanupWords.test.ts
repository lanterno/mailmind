import { describe, expect, it } from "vitest";
import { sendsUnsubscribe } from "./cleanupWords";

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

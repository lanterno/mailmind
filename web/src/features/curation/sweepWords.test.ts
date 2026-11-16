import { describe, expect, it } from "vitest";
import { undoBrings } from "./sweepWords";

describe("what a sweep says", () => {
  it("says Undo puts it all back, or the rest when some of it can't come back", () => {
    expect(undoBrings(0)).toBe("puts it all back");
    expect(undoBrings(undefined)).toBe("puts it all back");
    expect(undoBrings(null)).toBe("puts it all back");
    expect(undoBrings(2)).toBe("puts the rest back");
  });
});

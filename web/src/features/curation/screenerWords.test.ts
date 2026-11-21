import { describe, expect, it } from "vitest";
import { decisionWords } from "./screenerWords";

describe("what the Screener says of a decision", () => {
  it("calls a person by their first name", () => {
    expect(decisionWords("block", "Lin Mori", "lin@example.test", false)).toBe("Screened out Lin");
    expect(decisionWords("keep", "Lin Mori", "lin@example.test", false)).toBe("Allowed Lin");
  });

  it("calls an organisation by its whole name", () => {
    expect(decisionWords("keep", "Thimble & Co", "orders@thimble.example.test", false)).toBe("Allowed Thimble & Co");
    expect(decisionWords("block", "Parcel Service", "tracking@parcel.example.test", true)).toBe("Screened out Parcel Service · trashed");
    // Its address carries the last word of its name: never "Screened out Guild".
    expect(decisionWords("block", "Guild Gazette", "gazette@example.test", false)).toBe("Screened out Guild Gazette");
  });

  it("calls one with no name by their whole address, never its part before the @ alone", () => {
    expect(decisionWords("block", "", "lin@example.test", false)).toBe("Screened out lin@example.test");
    expect(decisionWords("keep", null, "lin@example.test", false)).toBe("Allowed lin@example.test");
    expect(decisionWords("keep", "lin@example.test", "lin@example.test", false)).toBe("Allowed lin@example.test");
  });
});

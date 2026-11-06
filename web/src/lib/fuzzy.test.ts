import { describe, expect, it } from "vitest";
import { fuzzyScore, rankByLabel } from "./fuzzy";

describe("fuzzyScore", () => {
  it("takes the letters in order, or not at all", () => {
    expect(fuzzyScore("arc", "Archive")).toBeGreaterThan(0);
    expect(fuzzyScore("cra", "Archive")).toBe(-Infinity);
    expect(fuzzyScore("", "Archive")).toBe(0);
  });
  it("likes a run, and the start of a word", () => {
    expect(fuzzyScore("ad", "Ada / Inbox")).toBeGreaterThan(fuzzyScore("ad", "Bread"));
    expect(fuzzyScore("inb", "Ada / Inbox")).toBeGreaterThan(fuzzyScore("inb", "Ada in my bag"));
  });
  it("breaks a tie for the shorter", () => {
    expect(fuzzyScore("star", "Star")).toBeGreaterThan(fuzzyScore("star", "Star this one"));
  });
});

describe("rankByLabel", () => {
  const entries = [{ label: "Mark junk" }, { label: "Archive" }, { label: "Undo last action" }];
  it("keeps the order with no query", () => expect(rankByLabel("", entries)).toBe(entries));
  it("drops what doesn't match and puts the best first", () => {
    expect(rankByLabel("a", entries).map((e) => e.label)[0]).toBe("Archive");
    expect(rankByLabel("arch", entries).map((e) => e.label)).toEqual(["Archive"]);
  });
});

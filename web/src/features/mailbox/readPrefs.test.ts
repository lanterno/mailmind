import { describe, expect, it } from "vitest";
import { parsePrefs } from "./readPrefs";

describe("reading settings", () => {
  it("start from the defaults", () => {
    expect(parsePrefs(null, null)).toEqual({ width: "medium", size: "m", font: "sans", colors: "auto" });
  });
  it("read what was saved", () => {
    expect(parsePrefs('{"width":"wide","size":"xl","font":"mono"}', "dark")).toEqual({ width: "wide", size: "xl", font: "mono", colors: "dark" });
  });
  it("ignore a value they don't know, and one that won't parse", () => {
    expect(parsePrefs('{"width":"huge","font":"constructor"}', "neon").width).toBe("medium");
    expect(parsePrefs("{nope", null).font).toBe("sans");
    expect(parsePrefs("null", null).size).toBe("m");
  });
});

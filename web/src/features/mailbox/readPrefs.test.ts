import { describe, expect, it } from "vitest";
import { PREFERENCE_DEFAULTS } from "../../lib/api/limits.gen";
import type { Prefs } from "../../lib/prefs";
import { fromPrefs } from "./readPrefs";

const prefs = (over: object): Prefs => ({ ...PREFERENCE_DEFAULTS, ...over }) as Prefs;

describe("reading settings", () => {
  it("start from the defaults", () => {
    expect(fromPrefs(prefs({}))).toEqual({ width: "medium", size: "m", font: "sans", colors: "auto" });
  });
  it("read what was saved", () => {
    expect(fromPrefs(prefs({ read_width: "wide", read_size: "xl", read_font: "mono", mail_colors: "dark" }))).toEqual({ width: "wide", size: "xl", font: "mono", colors: "dark" });
  });
  it("ignore a value they don't know, which a browser's old key can still hold", () => {
    expect(fromPrefs(prefs({ read_width: "huge", read_font: "constructor", mail_colors: "neon" }))).toEqual({ width: "medium", size: "m", font: "sans", colors: "auto" });
  });
});

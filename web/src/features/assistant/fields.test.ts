import { describe, expect, it } from "vitest";
import { dueWords, fromField, localDay, toField } from "./fields";

const two = (n: number) => String(n).padStart(2, "0");
const localOf = (iso: string) => {
  const d = new Date(iso);
  return `${d.getFullYear()}-${two(d.getMonth() + 1)}-${two(d.getDate())}T${two(d.getHours())}:${two(d.getMinutes())}`;
};

describe("toField", () => {
  it("shows a time named in no zone as its clock reading", () => {
    expect(toField("2026-11-12T15:00", false)).toBe("2026-11-12T15:00");
    expect(toField("2026-11-12T15:00:30", false)).toBe("2026-11-12T15:00");
  });
  it("shows a time named in a zone as it falls here", () => {
    expect(toField("2026-11-12T20:00+00:00", false)).toBe(localOf("2026-11-12T20:00:00Z"));
    expect(toField("2026-11-12T15:00-05:00", false)).toBe(localOf("2026-11-12T20:00:00Z"));
    expect(toField("2026-11-12T20:00Z", false)).toBe(localOf("2026-11-12T20:00:00Z"));
  });
  it("shows a day as a day", () => {
    expect(toField("2026-11-12", true)).toBe("2026-11-12");
  });
  it("shows nothing for nothing, or for what won't read", () => {
    expect(toField(null, false)).toBe("");
    expect(toField("", true)).toBe("");
    expect(toField("rubbish+00:00", false)).toBe("");
  });
});

describe("fromField", () => {
  it("makes a time the instant it is here", () => {
    const field = "2026-11-12T15:00";
    expect(fromField(field, false)).toBe(new Date(field).toISOString());
  });
  it("leaves a day as a day, and nothing as nothing", () => {
    expect(fromField("2026-11-12", true)).toBe("2026-11-12");
    expect(fromField("", false)).toBe("");
  });
  it("leaves what won't read for the server to refuse", () => {
    expect(fromField("rubbish", false)).toBe("rubbish");
  });
});

describe("dueWords", () => {
  it("says nothing with no date", () => {
    expect(dueWords(null, "2026-11-09")).toEqual({ words: "", late: false });
  });
  it("names the day of a date ahead, and today", () => {
    expect(dueWords("2026-11-20", "2026-11-09")).toEqual({ words: "Fri, Nov 20", late: false });
    expect(dueWords("2026-11-09", "2026-11-09")).toEqual({ words: "Mon, Nov 9 · today", late: false });
  });
  it("says a date past is overdue", () => {
    expect(dueWords("2026-11-02", "2026-11-09")).toEqual({ words: "Mon, Nov 2 · overdue", late: true });
  });
});

describe("localDay", () => {
  it("is the local date", () => {
    expect(localDay(new Date(2026, 10, 9, 23, 30))).toBe("2026-11-09");
  });
});

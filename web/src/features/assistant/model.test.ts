import { describe, expect, it } from "vitest";
import type { DigestItem } from "../mailbox";
import { commitments, eventWhen, meetingWhen, greeting, nameIn, needsNote, waitAge } from "./model";

const item = (key: number, over: Partial<DigestItem> = {}) => ({ key, folder: "INBOX", uid: key, importance: 1, needs_you: false, safe_to_file: false, commitment_worthy: false, ...over }) as DigestItem;

describe("greeting", () => {
  it.each([[3, "Working late"], [4, "Working late"], [5, "Good morning"], [11, "Good morning"], [12, "Good afternoon"], [17, "Good afternoon"], [18, "Good evening"], [23, "Good evening"]])(
    "at %i says %s", (hour, words) => expect(greeting(hour)).toBe(words));
});

describe("what the digest asks of you", () => {
  it("sorts commitments by importance", () => {
    const got = commitments([item(1, { commitment_worthy: true, importance: 2 }), item(2, { commitment_worthy: true, importance: 4 }), item(3)]);
    expect(got.map((x) => x.key)).toEqual([2, 1]);
  });
  it("words the count", () => {
    expect(needsNote(0)).toBe("Nothing needs you");
    expect(needsNote(1)).toBe("1 needs you");
    expect(needsNote(2)).toBe("2 need you");
  });
});

describe("the side column's words", () => {
  const now = new Date(2026, 10, 2, 12, 0);
  it("says when a week's meeting or deadline is", () => {
    expect(meetingWhen({ start: "2026-11-03T14:05", all_day: false })).toMatch(/Nov.*2:05/);
    expect(meetingWhen({ start: "2026-11-03T00:00", all_day: true })).toMatch(/Nov.*all day$/);
  });
  it("says when an event is", () => {
    expect(eventWhen({ start: new Date(2026, 10, 2, 15, 0).toISOString(), all_day: false }, now)[1]).toBe("today");
    expect(eventWhen({ start: new Date(2026, 10, 2).toISOString(), all_day: true }, now)).toEqual(["Today", "all day"]);
    expect(eventWhen({ start: new Date(2026, 10, 4).toISOString(), all_day: true }, now)[1]).toBe("all day");
  });
  it("counts days waited", () => {
    const at = now.getTime() / 1000;
    expect(waitAge(at - 3600, now)).toBe("today");
    expect(waitAge(at - 86400, now)).toBe("1 day");
    expect(waitAge(at - 3 * 86400, now)).toBe("3 days");
  });
  it("reads a name out of an address", () => {
    expect(nameIn('"Lin Park" <lin@example.test>')).toBe("Lin Park");
    expect(nameIn("<lin@example.test>")).toBe("lin@example.test");
    expect(nameIn(null)).toBe("");
  });
});

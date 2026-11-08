import { describe, expect, it } from "vitest";
import { commitments, eventWhen, fromDigest, greeting, nameIn, needsNote, needsYou, safeToFile, topicItems, waitAge, type DigestItem } from "./model";

const item = (key: number, over: Partial<DigestItem> = {}) => ({ key, folder: "INBOX", importance: 1, needs_you: false, safe_to_file: false, commitment_worthy: false, ...over }) as DigestItem;

describe("greeting", () => {
  it.each([[3, "Working late"], [4, "Working late"], [5, "Good morning"], [11, "Good morning"], [12, "Good afternoon"], [17, "Good afternoon"], [18, "Good evening"], [23, "Good evening"]])(
    "at %i says %s", (hour, words) => expect(greeting(hour)).toBe(words));
});

describe("what the digest asks of you", () => {
  it("lists what needs you, most important first, then newest", () => {
    const got = needsYou([item(1, { needs_you: true, importance: 3 }), item(2), item(3, { needs_you: true, importance: 5 }), item(4, { needs_you: true, importance: 3 })]);
    expect(got.map((x) => x.key)).toEqual([3, 4, 1]);
  });
  it("takes what the server says is safe to file, and works nothing out", () => {
    expect(safeToFile([item(1, { safe_to_file: true }), item(2, { importance: 1 })]).map((x) => x.key)).toEqual([1]);
  });
  it("sorts commitments by importance", () => {
    const got = commitments([item(1, { commitment_worthy: true, importance: 2 }), item(2, { commitment_worthy: true, importance: 4 }), item(3)]);
    expect(got.map((x) => x.key)).toEqual([2, 1]);
  });
  it("words the count", () => {
    expect(needsNote(0)).toBe("Nothing needs you");
    expect(needsNote(1)).toBe("1 needs you");
    expect(needsNote(2)).toBe("2 need you");
  });
  it("drops a topic's mail the digest no longer holds, and keeps the group's order", () => {
    const named = [3, 9, 1].map((key) => ({ key, folder: "INBOX" }));
    expect(topicItems(named, [item(1), item(3)]).map((x) => x.key)).toEqual([3, 1]);
  });
  it("takes a topic's mail by its copy, not a copy of it in another folder", () => {
    expect(topicItems([{ key: 3, folder: "Archive" }], [item(3)])).toEqual([]);
  });
  it("makes a list row of an item, in no day section", () => {
    const row = fromDigest(item(7, { action: "  Reply ", from_name: "Lin", thread_id: "t1", ts: 100 }));
    expect(row).toMatchObject({ id: "7:INBOX", key: 7, folder: "INBOX", action: "Reply", fromName: "Lin", threadId: "t1", ts: null, count: 1 });
  });
});

describe("the side column's words", () => {
  const now = new Date(2026, 10, 2, 12, 0);
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

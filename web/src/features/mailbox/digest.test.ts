import { describe, expect, it } from "vitest";
import { fromDigest, needsYou, safeToFile, topicItems, type DigestItem } from "./digest";

const item = (key: number, over: Partial<DigestItem> = {}) => ({ key, folder: "INBOX", uid: key, importance: 1, needs_you: false, safe_to_file: false, commitment_worthy: false, ...over }) as DigestItem;

describe("what the digest asks of you", () => {
  it("lists what needs you, most important first, then newest", () => {
    const got = needsYou([item(1, { needs_you: true, importance: 3 }), item(2), item(3, { needs_you: true, importance: 5 }), item(4, { needs_you: true, importance: 3 })]);
    expect(got.map((x) => x.key)).toEqual([3, 4, 1]);
  });
  it("takes what the server says is safe to file, and works nothing out", () => {
    expect(safeToFile([item(1, { safe_to_file: true }), item(2, { importance: 1 })]).map((x) => x.key)).toEqual([1]);
  });
  it("drops a topic's mail the digest no longer holds, and keeps the group's order", () => {
    const named = [3, 9, 1].map((key) => ({ key, folder: "INBOX", uid: key }));
    expect(topicItems(named, [item(1), item(3)]).map((x) => x.key)).toEqual([3, 1]);
  });
  it("takes a topic's mail by its copy, not a copy of it in another folder or beside it", () => {
    expect(topicItems([{ key: 3, folder: "Archive", uid: 3 }], [item(3)])).toEqual([]);
    expect(topicItems([{ key: 3, folder: "INBOX", uid: 8 }], [item(3)])).toEqual([]);
  });
  it("makes a list row of an item, in no day section", () => {
    const row = fromDigest(item(7, { action: "  Reply ", from_name: "Lin", thread_id: "t1", ts: 100 }));
    expect(row).toMatchObject({ id: "7:7:INBOX", key: 7, folder: "INBOX", uid: 7, action: "Reply", fromName: "Lin", threadId: "t1", ts: null, count: 1 });
  });
});

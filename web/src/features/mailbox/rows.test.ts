import { describe, expect, it } from "vitest";
import { filedWords, folderTitle, fromFolder, fromSearch, inDaySections, inTab, isConversation, roleLabel, type FolderRow, type Row, type SearchRow } from "./rows";

const folderRow = (o: Partial<FolderRow> = {}): FolderRow => ({
  pk: 1, key: 1, folder: "INBOX", bucket: "PERSONAL", flagged: false, from_addr: "bo@example.test", from_name: "Bo", has_attach: false,
  is_junk: false, seen: false, snippet: "s", subject: "Hi", thread_id: "t1", when: "Mon", ...o,
});
const row = (o: Partial<Row> = {}): Row => ({ ...fromFolder(folderRow(), "a@example.test"), ...o });

describe("a row", () => {
  it("takes the account it was asked for, unless it says its own, and is named by its copy", () => {
    expect(fromFolder(folderRow(), "a@example.test")).toMatchObject({ id: "1:INBOX", key: 1, account: "a@example.test", folder: "INBOX", count: 1, action: "", ts: null });
    expect(fromFolder(folderRow({ account: "b@example.test", folder: "Later" }), "a@example.test")).toMatchObject({ id: "1:Later", account: "b@example.test", folder: "Later" });
  });
  it("says how long it has waited in place of when it came", () => {
    expect(fromFolder(folderRow({ waiting_label: "3 days" }), "a").when).toBe("3 days");
  });
  it("trims an action that is only space, so it asks nothing of you", () => {
    expect(fromFolder(folderRow({ action: "  " }), "a").action).toBe("");
  });
  it("comes from a search as a single message", () => {
    const hit = { ...folderRow(), account: "a", alias: "a", commitment_worthy: false, date: "", folder: "Later", importance: null, needs_you: true, reply_worthy: false, safe_to_file: false } as SearchRow;
    expect(fromSearch(hit)).toMatchObject({ id: "1:Later", key: 1, folder: "Later", threadId: folderRow().thread_id, count: 1, needsYou: true });
  });
});

describe("a conversation row", () => {
  it("needs grouping, more than one message and a thread", () => {
    expect(isConversation(row({ count: 3 }), true)).toBe(true);
    expect(isConversation(row({ count: 3 }), false)).toBe(false);
    expect(isConversation(row({ count: 1 }), true)).toBe(false);
    expect(isConversation(row({ count: 3, threadId: "" }), true)).toBe(false);
  });
  it("isn't one in a draft folder, whose older copies are no conversation", () => {
    expect(isConversation(row({ count: 3, folder: "Drafts" }), true)).toBe(false);
  });
});

describe("the Inbox's tabs", () => {
  const rows = [row({ id: "1:INBOX", key: 1, needsYou: true, seen: true }), row({ id: "2:INBOX", key: 2, seen: false }), row({ id: "3:INBOX", key: 3, seen: true })];
  it("filters by what each asks", () => {
    expect(inTab(rows, "needs", null).map((r) => r.key)).toEqual([1]);
    expect(inTab(rows, "unread", null).map((r) => r.key)).toEqual([2]);
    expect(inTab(rows, "all", null).map((r) => r.key)).toEqual([1, 2, 3]);
  });
  it("keeps the open message in place", () => {
    expect(inTab(rows, "needs", "3:INBOX").map((r) => r.key)).toEqual([1, 3]);
    expect(inTab(rows, "unread", "3:INBOX").map((r) => r.key)).toEqual([2, 3]);
  });
  it("keeps only the open copy, not another copy of its message", () => {
    expect(inTab(rows, "needs", "3:Archive").map((r) => r.key)).toEqual([1]);
  });
});

describe("day sections", () => {
  it("go on a real folder sorted newest first", () => {
    expect(inDaySections([row({ ts: 5 }), row({ ts: 3 })], "INBOX")).toBe(true);
    expect(inDaySections([row({ ts: 3 }), row({ ts: 5 })], "INBOX")).toBe(false);
    expect(inDaySections([row({ ts: 5 }), row({ ts: null })], "INBOX")).toBe(false);
    expect(inDaySections([row({ ts: 5 })], "__waiting__")).toBe(false);
  });
});

describe("a folder's name", () => {
  const folders = [{ name: "[Gmail]/All Mail", role: "all", total: 1, unseen: 0 }, { name: "Lists", role: "custom", total: 1, unseen: 0 }, { name: "Sent Items", role: "sent", total: 1, unseen: 0 }];
  it("is its role's, or its own when it plays none", () => {
    expect(folders.map(roleLabel)).toEqual(["All mail", "Lists", "Sent"]);
  });
  it("titles a virtual folder, and falls back to the name", () => {
    expect(folderTitle("__waiting__", folders)).toBe("Waiting");
    expect(folderTitle("Sent Items", folders)).toBe("Sent");
    expect(folderTitle("Gone", folders)).toBe("Gone");
  });
});

describe("what filing says", () => {
  it("counts a conversation, and what stayed", () => {
    expect(filedWords("Archived", {}, false)).toBe("Archived");
    expect(filedWords("Archived", { count: 3 }, false)).toBe("Archived 3 messages");
    expect(filedWords("Archived", { count: 2, failed: 1 }, false)).toBe("Archived 2 of 3 — the rest stayed");
  });
  it("says when it will land", () => {
    expect(filedWords("Trashed", { status: "queued" }, false)).toBe("Trashed · once the server is back");
    expect(filedWords("Trashed", { queued: true }, true)).toBe("Trashed · waits for a full run");
  });
});

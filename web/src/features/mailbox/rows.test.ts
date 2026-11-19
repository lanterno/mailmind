import { describe, expect, it } from "vitest";
import { mineSet } from "../../lib/mine";
import { filedWords, folderTitle, fromFolder, fromSearch, inDaySections, inTab, isConversation, labelQuery, roleLabel, whoOf, type FolderRow, type Row, type SearchRow } from "./rows";

const folderRow = (o: Partial<FolderRow> = {}): FolderRow => ({
  key: 1, folder: "INBOX", uid: 1, account: "a@example.test", bucket: "PERSONAL", flagged: false, from_addr: "bo@example.test", from_name: "Bo", has_attach: false,
  is_junk: false, seen: false, snippet: "s", subject: "Hi", thread_id: "t1", when: "Mon", sent_to: [{ name: "Ada Park", addr: "a@example.test" }],
  sent_to_more: 0, ...o,
});
const row = (o: Partial<Row> = {}): Row => ({ ...fromFolder(folderRow()), ...o });

describe("a row", () => {
  it("says its own account, and is named by its copy", () => {
    expect(fromFolder(folderRow())).toMatchObject({ id: "1:1:INBOX", key: 1, account: "a@example.test", folder: "INBOX", uid: 1, count: 1, action: "", ts: null });
    expect(fromFolder(folderRow({ account: "b@example.test", folder: "Later" }))).toMatchObject({ id: "1:1:Later", account: "b@example.test", folder: "Later" });
    expect(fromFolder(folderRow({ uid: 2 })).id).not.toBe(fromFolder(folderRow()).id);
  });
  it("says how long it has waited in place of when it came", () => {
    expect(fromFolder(folderRow({ waiting_label: "3 days" })).when).toBe("3 days");
  });
  it("trims an action that is only space, so it asks nothing of you", () => {
    expect(fromFolder(folderRow({ action: "  " })).action).toBe("");
  });
  it("comes from a search as a single message", () => {
    const hit = { ...folderRow(), account: "a", alias: "a", commitment_worthy: false, date: "", folder: "Later", importance: null, needs_you: true, reply_worthy: false, safe_to_file: false } as SearchRow;
    expect(fromSearch(hit)).toMatchObject({ id: "1:1:Later", key: 1, folder: "Later", uid: 1, threadId: folderRow().thread_id, count: 1, needsYou: true });
  });
});

describe("whom a row names", () => {
  const mine = mineSet(["ada@example.test", "ada@work.example.test"]);
  const lin = { name: "Lin Mori", addr: "lin@example.test" }, bea = { name: null, addr: "bea@example.test" };
  const yours = (o: Partial<Row> = {}) => row({ fromName: "Ada Park", fromAddr: "ada@example.test", sentTo: [lin], sentToMore: 0, ...o });

  it("is the sender of mail from someone else, whoever it went to", () => {
    expect(whoOf(row({ sentTo: [lin, bea], sentToMore: 3 }), mine)).toEqual({ name: "Bo", avatarName: "Bo", avatarAddr: "bo@example.test" });
    expect(whoOf(row({ fromName: null }), mine).name).toBe("bo@example.test");
  });
  it("is whom your own mail went to, by first name, and the first of them in the avatar", () => {
    expect(whoOf(yours(), mine)).toEqual({ name: "To Lin", avatarName: "Lin Mori", avatarAddr: "lin@example.test" });
  });
  it("names two and counts the rest, an address where no name was written", () => {
    expect(whoOf(yours({ sentTo: [lin, bea], sentToMore: 2 }), mine).name).toBe("To Lin, bea@example.test +2");
    expect(whoOf(yours({ sentTo: [bea, lin] }), mine).name).toBe("To bea@example.test, Lin");
  });
  it("says an organisation's name whole", () => {
    expect(whoOf(yours({ sentTo: [{ name: "Parcel Service", addr: "help@parcel.example.test" }] }), mine).name).toBe("To Parcel Service");
  });
  it("is yours from any of your accounts, tagged or in capitals", () => {
    for (const fromAddr of ["ada+news@example.test", "Ada@Work.example.test"]) expect(whoOf(yours({ fromAddr }), mine).name).toBe("To Lin");
  });
  it("leaves you out of whom it went to", () => {
    expect(whoOf(yours({ sentTo: [{ name: "Ada", addr: "ADA@example.test" }, lin], sentToMore: 1 }), mine).name).toBe("To Lin +1");
  });
  it("is you where your own went to no one else, or names no one yet", () => {
    expect(whoOf(yours({ sentTo: [{ name: "Ada", addr: "ada+notes@example.test" }] }), mine).name).toBe("Ada Park");
    expect(whoOf(yours({ sentTo: [] }), mine).name).toBe("Ada Park");
  });
  it("is the sender while your addresses aren't known yet", () => {
    expect(whoOf(yours(), new Set()).name).toBe("Ada Park");
  });
  it("comes with a row from a folder and from a search", () => {
    expect(fromFolder(folderRow({ sent_to: [lin], sent_to_more: 4 }))).toMatchObject({ sentTo: [lin], sentToMore: 4 });
    const hit = { ...folderRow({ sent_to: [bea], sent_to_more: 1 }), alias: null, commitment_worthy: false, date: "", importance: null, needs_you: false, reply_worthy: false, safe_to_file: false } as SearchRow;
    expect(fromSearch(hit)).toMatchObject({ sentTo: [bea], sentToMore: 1 });
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
  it("counts what opening it shows, apart from what filing it takes", () => {
    expect(fromFolder(folderRow({ count: 2, thread_count: 3 }))).toMatchObject({ count: 2, threadCount: 3 });
  });
  it("counts what a list that says one number holds", () => {
    expect(fromFolder(folderRow({ count: 4 }))).toMatchObject({ count: 4, threadCount: 4 });
    expect(fromFolder(folderRow())).toMatchObject({ count: 1, threadCount: 1 });
  });
});

describe("the Inbox's tabs", () => {
  const rows = [row({ id: "1:1:INBOX", key: 1, needsYou: true, seen: true }), row({ id: "2:2:INBOX", key: 2, uid: 2, seen: false }), row({ id: "3:3:INBOX", key: 3, uid: 3, seen: true })];
  it("filters by what each asks", () => {
    expect(inTab(rows, "needs", null).map((r) => r.key)).toEqual([1]);
    expect(inTab(rows, "unread", null).map((r) => r.key)).toEqual([2]);
    expect(inTab(rows, "all", null).map((r) => r.key)).toEqual([1, 2, 3]);
  });
  it("keeps the open message in place", () => {
    expect(inTab(rows, "needs", "3:3:INBOX").map((r) => r.key)).toEqual([1, 3]);
    expect(inTab(rows, "unread", "3:3:INBOX").map((r) => r.key)).toEqual([2, 3]);
  });
  it("keeps only the open copy, not another copy of its message", () => {
    expect(inTab(rows, "needs", "3:3:Archive").map((r) => r.key)).toEqual([1]);
    expect(inTab(rows, "needs", "3:9:INBOX").map((r) => r.key)).toEqual([1]);
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
  const folders = [
    { name: "[Gmail]/All Mail", own_name: "All Mail", role: "all", total: 1, unseen: 0, takes_filing: false, changeable: false },
    { name: "Lists", own_name: "Lists", role: "custom", total: 1, unseen: 0, takes_filing: true, changeable: true },
    { name: "Sent Items", own_name: "Sent Items", role: "sent", total: 1, unseen: 0, takes_filing: false, changeable: false },
  ];
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
    expect(filedWords("Archived", {})).toBe("Archived");
    expect(filedWords("Archived", { count: 3 })).toBe("Archived 3 messages");
    expect(filedWords("Archived", { count: 2, failed: 1 })).toBe("Archived 2 of 3 — the rest stayed");
  });
  it("says where it went, when a person picked that", () => {
    expect(filedWords("Moved", {}, "Clients")).toBe("Moved to Clients");
    expect(filedWords("Moved", { count: 3 }, "Clients")).toBe("Moved 3 messages to Clients");
    expect(filedWords("Moved", { count: 2, failed: 1 }, "Clients")).toBe("Moved 2 of 3 to Clients — the rest stayed");
    expect(filedWords("Moved", { status: "queued" }, "Clients")).toBe("Moved to Clients · once the server is back");
  });
  it("says when it will land: once the server is back, and never a full run, since read only queues nothing", () => {
    expect(filedWords("Trashed", { status: "queued" })).toBe("Trashed · once the server is back");
    expect(filedWords("Trashed", { queued: true })).toBe("Trashed · once the server is back");
  });
});

describe("a label's query", () => {
  it("reads back through the search grammar's quoting", () => {
    expect(labelQuery("Travel")).toBe('label:"Travel"');
    expect(labelQuery("Two words")).toBe('label:"Two words"');
    expect(labelQuery('Say "hi"')).toBe('label:"Say \\"hi\\""');
    expect(labelQuery("back\\slash")).toBe('label:"back\\\\slash"');
  });
});

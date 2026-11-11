import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Mailbox } from "../features/mailbox/useMailbox";
import { buildCommands, commandForKey, goChord, goKeys, legendOf, onSavedView } from "./commands";
import { go } from "./route";

// Everything a command sets off, as something a test can see it do.
const the = vi.hoisted(() => ({
  toast: vi.fn(), answer: vi.fn(), reply: vi.fn(), forward: vi.fn(), compose: vi.fn(), summarize: vi.fn(), suggest: vi.fn(),
  toggleMark: vi.fn(), setGrouping: vi.fn(), setTheme: vi.fn(), snoozeAsk: vi.fn(), panel: vi.fn(), doing: vi.fn(),
  helpOpen: vi.fn(), curateOpen: vi.fn(), filterForm: vi.fn(), syncNow: vi.fn(), reconcile: vi.fn(), setReadFocus: vi.fn(),
  theme: "day", focusing: false,
}));
vi.mock("./route", async (real) => ({ ...(await real<typeof import("./route")>()), go: vi.fn() }));
vi.mock("../lib/toast", () => ({ toast: the.toast }));
vi.mock("../lib/theme", () => ({ theme: { get: () => the.theme }, setTheme: the.setTheme, THEME_LABEL: { day: "Day", dusk: "Dusk", night: "Night" } }));
vi.mock("../features/assistant/Panels", () => ({ panel: { set: the.panel } }));
vi.mock("../features/assistant/ask", () => ({ doing: { set: the.doing } }));
vi.mock("../features/assistant/Suggestions", () => ({ findSuggestions: the.suggest }));
vi.mock("../features/compose/compose", () => ({ composing: { open: the.compose, reply: the.reply, forward: the.forward } }));
vi.mock("../features/compose/draftAhead", () => ({ answer: the.answer }));
vi.mock("../features/curation/open", () => ({ curateOpen: { set: the.curateOpen }, filterForm: { set: the.filterForm }, newFilter: () => ({ fresh: true }) }));
vi.mock("../features/mailbox/actions", () => ({ toggleMark: the.toggleMark }));
vi.mock("../features/mailbox/useMailbox", () => ({ setGrouping: the.setGrouping }));
vi.mock("../features/reader/AiCard", () => ({ summarize: the.summarize }));
vi.mock("../features/reader/Reader", () => ({ readFocus: { get: () => the.focusing }, setReadFocus: the.setReadFocus }));
vi.mock("../features/attention/SnoozeMenu", () => ({ snoozeAsk: { set: the.snoozeAsk } }));
vi.mock("../features/status/Help", () => ({ helpOpen: { set: the.helpOpen } }));
vi.mock("../features/status/Status", () => ({ syncNow: the.syncNow, reconcile: the.reconcile }));
vi.mock("./FolderRail", () => ({ viewQuery: (v: { q: string }) => v.q }));

const A = "ada@example.test", B = "bo@example.test";
const open = { key: 7, folder: "INBOX", uid: 70 };
const folders = [
  { name: "INBOX", role: "inbox" }, { name: "Sent", role: "sent" }, { name: "[Gmail]/All Mail", role: "all" }, { name: "Lists", role: "custom" },
];

// A mailbox as the commands see it: what is open and marked, where you are, and each verb as a spy.
function mailbox(over: Partial<Record<keyof Mailbox, unknown>> = {}): Mailbox {
  const verbs = Object.fromEntries([
    "goToFolder", "move", "moveUnread", "bulk", "fileOpen", "askMove", "star", "unread", "mute", "unmute", "replyLater", "replyLaterDone",
    "dismissFollowup", "undo", "showBrief", "openSettings",
  ].map((name) => [name, vi.fn()]));
  return {
    ...verbs,
    marks: new Set<string>(), markedRows: [], openId: null, folder: "INBOX", accounts: [A], aliases: {}, everyFolder: new Map(),
    folders: { folders }, views: [], source: { kind: "folder", account: A, folder: "INBOX" }, account: A, inInbox: true, searching: false,
    grouped: true, qc: {}, target: () => open, replyTo: () => open,
    ...over,
  } as unknown as Mailbox;
}
const reading = (over: Partial<Record<keyof Mailbox, unknown>> = {}) => mailbox({ openId: "7:70:INBOX", ...over });
const marking = (over: Partial<Record<keyof Mailbox, unknown>> = {}) => mailbox({ marks: new Set(["1:10:INBOX", "2:20:INBOX"]), markedRows: [{ key: 1, folder: "INBOX", uid: 10 }, { key: 2, folder: "INBOX", uid: 20 }], ...over });
const ui = { toggleRail: vi.fn(), saveSearch: vi.fn(), createFilter: vi.fn(), openSearchBar: vi.fn() };
const ids = (m: Mailbox) => buildCommands(m, ui).map((c) => c.id);
const command = (m: Mailbox, id: string) => buildCommands(m, ui).find((c) => c.id === id)!;
const pressed = (m: Mailbox, key: string) => commandForKey(buildCommands(m, ui), key);

beforeEach(() => {
  vi.clearAllMocks();
  the.theme = "day";
  the.focusing = false;
  vi.stubGlobal("document", { querySelector: () => null });
});
afterEach(() => vi.unstubAllGlobals());

describe("g then a letter", () => {
  it("goes to the folder that plays that part on this account", () => {
    const m = mailbox();
    goChord(m, "i");
    goChord(m, "s");
    expect(vi.mocked(m.goToFolder).mock.calls).toEqual([["INBOX"], ["Sent"]]);
  });
  it("archives into All Mail on an account with no Archive, as Gmail does", () => {
    const m = mailbox();
    goChord(m, "a");
    expect(m.goToFolder).toHaveBeenCalledWith("[Gmail]/All Mail");
  });
  it("prefers the Archive where there is one", () => {
    const m = mailbox({ folders: { folders: [...folders, { name: "Archive", role: "archive" }] } });
    goChord(m, "a");
    expect(m.goToFolder).toHaveBeenCalledWith("Archive");
  });
  it("says so when the account has no such folder", () => {
    const m = mailbox();
    goChord(m, "k");
    expect(m.goToFolder).not.toHaveBeenCalled();
    expect(the.toast).toHaveBeenCalledWith("No junk folder on this account");
  });
  it("goes to Snoozed, Waiting and Reply later whatever the account holds", () => {
    const m = mailbox({ folders: undefined });
    for (const key of ["z", "w", "l"]) goChord(m, key);
    expect(vi.mocked(m.goToFolder).mock.calls).toEqual([["__snoozed__"], ["__waiting__"], ["__replylater__"]]);
  });
  it("does nothing for a letter that names nowhere", () => {
    const m = mailbox();
    goChord(m, "q");
    expect(m.goToFolder).not.toHaveBeenCalled();
    expect(the.toast).not.toHaveBeenCalled();
  });
  it("is named in the legend letter by letter, a virtual folder by its title", () => {
    expect(goKeys()).toContainEqual({ key: "i", where: "inbox" });
    expect(goKeys()).toContainEqual({ key: "l", where: "reply later" });
    expect(goKeys().map((g) => g.key)).toEqual(["i", "s", "a", "t", "k", "d", "z", "w", "l"]);
  });
});

describe("what the commands offer", () => {
  it("names no two commands alike, and no key presses two of them", () => {
    const all = buildCommands(reading({ views: [{ id: "v1", name: "Bills", q: "bills" }] }), ui, true);
    expect(new Set(all.map((c) => c.id)).size).toBe(all.length);
    const keys = all.flatMap((c) => c.keys ?? []);
    expect(new Set(keys).size).toBe(keys.length);
  });
  it("offers nothing to do to mail with nothing open or marked", () => {
    for (const id of ["archive", "trash", "star", "reply", "snooze", "mark"]) expect(ids(mailbox())).not.toContain(id);
    expect(ids(mailbox())).toEqual(expect.arrayContaining(["move-down", "compose", "search", "undo"]));
  });
  it("offers filing on what is marked, but not replying to it", () => {
    expect(ids(marking())).toEqual(expect.arrayContaining(["archive", "trash", "junk", "star", "snooze", "mute"]));
    expect(ids(marking())).not.toContain("reply");
  });
  it("reaches every account and each of its folders, by the account's own name", () => {
    const m = mailbox({ accounts: [A, B], aliases: { [A]: "Ada" }, everyFolder: new Map([[A, folders]]) });
    const labels = buildCommands(m, ui).filter((c) => c.group === "Navigation").map((c) => c.label);
    expect(labels).toEqual(expect.arrayContaining(["Account · Ada", "Ada / Inbox", "Ada / All mail", "Ada / Lists", "Ada / Snoozed", "Account · bo", "bo / Waiting"]));
    command(m, `fold:${A}:Lists`).run();
    expect(go).toHaveBeenCalledWith({ source: { kind: "folder", account: A, folder: "Lists" }, opened: null });
  });
  it("offers the two themes not in use", () => {
    the.theme = "dusk";
    expect(ids(mailbox()).filter((id) => id.startsWith("theme:"))).toEqual(["theme:day", "theme:night"]);
    command(mailbox(), "theme:night").run();
    expect(the.setTheme).toHaveBeenCalledWith("night");
    expect(the.toast).toHaveBeenCalledWith("Night theme");
  });
  it("makes each saved view a command, a lane's searched in its lane", () => {
    const m = mailbox({ views: [{ id: "v1", name: "Bills", q: "bills" }, { id: "__lane_feed__", name: "Feed", q: "list:yes" }] });
    expect(command(m, "view:__lane_feed__").label).toBe("Feed (lane)");
    command(m, "view:__lane_feed__").run();
    expect(go).toHaveBeenCalledWith({ source: { kind: "search", q: "list:yes", account: A, lane: "feed" }, opened: null });
    command(m, "view:v1").run();
    expect(go).toHaveBeenLastCalledWith({ source: { kind: "search", q: "bills", account: A }, opened: null });
  });
});

describe("filing", () => {
  it("e archives what is open", () => {
    const m = reading();
    pressed(m, "e")!.run();
    expect(m.fileOpen).toHaveBeenCalledWith("archive");
    expect(m.bulk).not.toHaveBeenCalled();
  });
  it("e archives what is marked, ahead of what is open", () => {
    const m = marking({ openId: "7:70:INBOX" });
    pressed(m, "e")!.run();
    expect(m.bulk).toHaveBeenCalledWith("archive");
    expect(m.fileOpen).not.toHaveBeenCalled();
  });
  it("deletes on d, #, Delete and Backspace alike", () => {
    const m = reading();
    for (const key of ["d", "#", "Delete", "Backspace"]) expect(pressed(m, key)!.id).toBe("trash");
  });
  it("mutes a thread, and unmutes one in Muted", () => {
    const m = reading();
    pressed(m, "w")!.run();
    expect(m.mute).toHaveBeenCalled();
    const muted = reading({ folder: "__muted__" });
    expect(pressed(muted, "w")!.label).toBe("Unmute thread");
    pressed(muted, "w")!.run();
    expect(muted.unmute).toHaveBeenCalled();
  });
  it("snoozes what is open from its own button, and Unsnooze is offered only in Snoozed", () => {
    pressed(reading(), "h")!.run();
    expect(the.snoozeAsk).toHaveBeenCalledWith(expect.objectContaining({ target: open, rows: ["7:70:INBOX"], canUnsnooze: false }));
    pressed(reading({ folder: "__snoozed__" }), "h")!.run();
    expect(the.snoozeAsk).toHaveBeenLastCalledWith(expect.objectContaining({ canUnsnooze: true }));
  });
  it("snoozes the marked copies together", () => {
    pressed(marking(), "h")!.run();
    expect(the.snoozeAsk).toHaveBeenCalledWith(expect.objectContaining({
      target: { copies: [{ key: 1, folder: "INBOX", uid: 10 }, { key: 2, folder: "INBOX", uid: 20 }] }, rows: ["1:10:INBOX", "2:20:INBOX"], canUnsnooze: false,
    }));
  });
});

describe("with a message open", () => {
  it("r answers it, with the reply written ahead if there is one", () => {
    pressed(reading(), "r")!.run();
    expect(the.answer).toHaveBeenCalledWith(open);
  });
  it("a and f reply to all and forward", () => {
    pressed(reading(), "a")!.run();
    pressed(reading(), "f")!.run();
    expect(the.reply).toHaveBeenCalledWith(open, true);
    expect(the.forward).toHaveBeenCalledWith(open);
  });
  it("replies to nothing when the conversation has no one to answer", () => {
    const m = reading({ replyTo: () => null });
    for (const key of ["r", "a", "f"]) pressed(m, key)!.run();
    expect(the.answer).not.toHaveBeenCalled();
    expect(the.reply).not.toHaveBeenCalled();
    expect(the.forward).not.toHaveBeenCalled();
  });
  it("offers Dismiss follow-up only in Waiting, and Done only in Reply later", () => {
    expect(ids(reading())).not.toContain("dismiss");
    expect(ids(reading({ folder: "__waiting__" }))).toContain("dismiss");
    const later = reading({ folder: "__replylater__" });
    expect(pressed(later, "l")!.label).toBe("Reply later — Done");
    pressed(later, "l")!.run();
    expect(later.replyLaterDone).toHaveBeenCalled();
    expect(later.replyLater).not.toHaveBeenCalled();
  });
  it("Enter goes into focus reading, and out again", () => {
    expect(pressed(reading(), "Enter")!.label).toMatch(/^Focus reading/);
    pressed(reading(), "Enter")!.run();
    expect(the.setReadFocus).toHaveBeenCalledWith(true);
    the.focusing = true;
    expect(pressed(reading(), "Enter")!.label).toBe("Leave focus reading");
  });
  it("x marks the open message", () => {
    pressed(reading(), "x")!.run();
    expect(the.toggleMark).toHaveBeenCalledWith("7:70:INBOX");
  });
});

describe("moving through the list", () => {
  it("says so when there's no unread mail further on", () => {
    const m = mailbox({ moveUnread: vi.fn(() => false) });
    pressed(m, "n")!.run();
    expect(the.toast).toHaveBeenCalledWith("No more unread below");
    pressed(m, "p")!.run();
    expect(the.toast).toHaveBeenCalledWith("No more unread above");
  });
  it("says nothing when it found one", () => {
    pressed(mailbox({ moveUnread: vi.fn(() => true) }), "n")!.run();
    expect(the.toast).not.toHaveBeenCalled();
  });
  it("finds nothing for a key no command has", () => expect(pressed(mailbox(), "q")).toBeUndefined());
});

describe("Today's brief", () => {
  it("shows over the Inbox you are in", () => {
    const m = mailbox();
    command(m, "brief").run();
    expect(m.showBrief).toHaveBeenCalled();
  });
  it("goes to this account's Inbox from anywhere else, or to All inboxes with no account in mind", () => {
    command(mailbox({ inInbox: false, folder: "Lists" }), "brief").run();
    expect(go).toHaveBeenCalledWith({ source: { kind: "folder", account: A, folder: "INBOX" }, opened: null });
    command(mailbox({ inInbox: false, account: undefined }), "brief").run();
    expect(go).toHaveBeenLastCalledWith({ source: { kind: "allInboxes" }, opened: null });
  });
});

describe("a search", () => {
  const search = { kind: "search", q: "from:bo", account: A };

  it("may be saved as a view, once", () => {
    expect(ids(mailbox({ searching: true, source: search }))).toContain("save-view");
    const saved = mailbox({ searching: true, source: search, views: [{ id: "v1", name: "Bo", q: "from:bo" }] });
    expect(onSavedView(saved)).toBe(true);
    expect(ids(saved)).not.toContain("save-view");
  });
  it("may become a filter only as a plain search, not a lane or a conversation", () => {
    expect(ids(mailbox({ searching: true, source: search }))).toContain("create-filter");
    expect(ids(mailbox({ searching: true, source: { ...search, lane: "feed" } }))).not.toContain("create-filter");
    expect(ids(mailbox({ searching: true, source: { ...search, thread: "t1" } }))).not.toContain("create-filter");
    expect(ids(mailbox())).not.toContain("create-filter");
  });
});

describe("the keys legend", () => {
  it("lists every command that has keys, whatever is open or marked now", () => {
    const legend = legendOf(mailbox(), ui);
    expect(legend.commands.every((c) => c.keys?.length)).toBe(true);
    expect(legend.commands.map((c) => c.id)).toEqual(expect.arrayContaining(["archive", "reply", "snooze", "save-view", "focus", "move-down"]));
    expect(legend.go).toEqual(goKeys());
  });
  it("runs nothing as it is built", () => {
    legendOf(reading(), ui);
    expect(go).not.toHaveBeenCalled();
    expect(the.toast).not.toHaveBeenCalled();
  });
});

// Everything you can do from here, in one list: the keys and the palette both read it, so a command means the same either
// way and neither can drift from the other.
import { panel } from "../features/assistant/Panels";
import { doing } from "../features/assistant/ask";
import { composing } from "../features/compose/compose";
import { curateOpen } from "../features/curation/open";
import { docsOpen } from "../features/discovery/open";
import { answer } from "../features/compose/draftAhead";
import * as actions from "../features/mailbox/actions";
import { summarize } from "../features/mailbox/AiCard";
import { viewQuery } from "../features/mailbox/FolderRail";
import { readFocus, setReadFocus } from "../features/mailbox/Reader";
import { roleLabel, VIRTUAL_TITLE } from "../features/mailbox/rows";
import { snoozeAsk } from "../features/attention/SnoozeMenu";
import { setGrouping, type Mailbox } from "../features/mailbox/useMailbox";
import { helpOpen } from "../features/status/Help";
import { reconcile, syncNow } from "../features/status/Status";
import { shortAddress } from "../lib/text";
import { setTheme, theme, THEME_LABEL } from "../lib/theme";
import { toast } from "../lib/toast";
import { go } from "./route";

export interface Command {
  id: string;
  group: "Navigation" | "Actions" | "Commands";
  label: string;
  keys?: string[];     // what presses it, by KeyboardEvent.key with letters lowered
  hint?: string;       // what the palette shows for it
  run: () => void;
}

const ROLE_KEYS: Record<string, string> = { i: "inbox", s: "sent", a: "archive", t: "trash", k: "junk", d: "drafts" };
const VIRTUAL_KEYS: Record<string, string> = { z: "__snoozed__", w: "__waiting__", l: "__replylater__" };

// g then a letter: the folder that plays that role on this account. Gmail archives into All Mail.
export function goChord(m: Mailbox, key: string) {
  if (VIRTUAL_KEYS[key]) return m.goToFolder(VIRTUAL_KEYS[key]!);
  const role = ROLE_KEYS[key];
  if (!role) return;
  const folders = m.folders?.folders ?? [];
  const found = folders.find((f) => f.role === role) ?? (role === "archive" ? folders.find((f) => f.role === "all") : undefined);
  if (found) m.goToFolder(found.name);
  else toast(`No ${role} folder on this account`);
}

export function buildCommands(m: Mailbox, ui: { toggleRail: () => void; saveSearch: () => void }): Command[] {
  const out: Command[] = [];
  const add = (c: Command) => out.push(c);
  const any = m.marks.size > 0;
  const on = m.openPk !== null;
  const nameOf = (a: string) => m.aliases[a] || shortAddress(a);

  add({ id: "all-inboxes", group: "Navigation", label: "All inboxes", run: () => go({ source: { kind: "allInboxes" }, opened: null }) });
  for (const a of m.accounts) {
    add({ id: `acct:${a}`, group: "Navigation", label: `Account · ${nameOf(a)}`, run: () => go({ source: { kind: "folder", account: a, folder: "INBOX" }, opened: null }) });
    if (a === m.account) {
      for (const f of m.folders?.folders ?? []) {
        add({ id: `fold:${a}:${f.name}`, group: "Navigation", label: `${nameOf(a)} / ${roleLabel(f)}`, run: () => go({ source: { kind: "folder", account: a, folder: f.name }, opened: null }) });
      }
    }
    for (const name of ["__snoozed__", "__waiting__"]) {
      add({ id: `fold:${a}:${name}`, group: "Navigation", label: `${nameOf(a)} / ${VIRTUAL_TITLE[name]}`, run: () => go({ source: { kind: "folder", account: a, folder: name }, opened: null }) });
    }
  }

  add({ id: "move-down", group: "Actions", label: "Next message", keys: ["j", "ArrowDown"], hint: "j", run: () => m.move(1) });
  add({ id: "move-up", group: "Actions", label: "Previous message", keys: ["k", "ArrowUp"], hint: "k", run: () => m.move(-1) });
  add({ id: "next-unread", group: "Actions", label: "Next unread", keys: ["n"], hint: "n", run: () => { if (!m.moveUnread(1)) toast("No more unread below"); } });
  add({ id: "prev-unread", group: "Actions", label: "Previous unread", keys: ["p"], hint: "p", run: () => { if (!m.moveUnread(-1)) toast("No more unread above"); } });

  if (on || any) {
    add({ id: "archive", group: "Actions", label: "Archive", keys: ["e"], hint: "e", run: () => (any ? m.bulk("archive") : m.fileOpen("archive")) });
    add({ id: "trash", group: "Actions", label: "Delete", keys: ["d", "#", "Delete", "Backspace"], hint: "d", run: () => (any ? m.bulk("trash") : m.fileOpen("trash")) });
    add({ id: "junk", group: "Actions", label: "Mark junk", keys: ["b"], hint: "b", run: () => (any ? m.bulk("junk") : m.fileOpen("junk")) });
    add({ id: "star", group: "Actions", label: "Star", keys: ["s"], hint: "s", run: () => (any ? m.bulk("star") : m.star()) });
    add({ id: "unread", group: "Actions", label: "Mark unread", keys: ["u"], hint: "u", run: () => (any ? m.bulk("unread") : m.unread()) });
    add({
      id: "snooze", group: "Actions", label: "Snooze…", keys: ["h"], hint: "h",
      run: () => {
        if (any) snoozeAsk.set({ target: { pks: [...m.marks] }, rows: [...m.marks], anchor: document.querySelector("#bulkbar"), canUnsnooze: false });
        else {
          const target = m.target();
          if (target && m.openPk !== null) snoozeAsk.set({ target, rows: [m.openPk], anchor: document.querySelector('#reader [data-ca="snooze"],#reader [data-tca="snooze"]'), canUnsnooze: m.folder === "__snoozed__" });
        }
      },
    });
    add({
      id: "mute", group: "Actions", label: m.folder === "__muted__" ? "Unmute thread" : "Mute thread", keys: ["w"], hint: "w",
      run: () => (any ? m.bulk("mute") : m.folder === "__muted__" ? m.unmute() : m.mute()),
    });
  }
  if (on) {
    const replying = (run: (pk: number) => unknown) => () => {
      const pk = m.replyPk();
      if (pk !== null) void run(pk);
    };
    add({ id: "reply", group: "Actions", label: "Reply", keys: ["r"], hint: "r", run: replying(answer) });
    add({ id: "reply-all", group: "Actions", label: "Reply all", keys: ["a"], hint: "a", run: replying((pk) => composing.reply(pk, true)) });
    add({ id: "forward", group: "Actions", label: "Forward", keys: ["f"], hint: "f", run: replying(composing.forward) });
    add({
      id: "later", group: "Actions", label: m.folder === "__replylater__" ? "Reply later — Done" : "Reply later", keys: ["l"], hint: "l",
      run: () => (m.folder === "__replylater__" ? m.replyLaterDone() : m.replyLater()),
    });
    if (m.folder === "__waiting__") add({ id: "dismiss", group: "Actions", label: "Dismiss follow-up", run: m.dismissFollowup });
    add({ id: "summarize", group: "Actions", label: "AI summarize", keys: ["i"], hint: "i", run: () => { const t = m.target(); if (t) void summarize(t); } });
    add({ id: "mark", group: "Actions", label: "Select", keys: ["x"], hint: "x", run: () => actions.toggleMark(m.openPk!) });
    add({
      id: "focus", group: "Commands", label: readFocus.get() ? "Leave focus reading" : "Focus reading — hide folders and list", keys: ["Enter"], hint: "↵",
      run: () => setReadFocus(!readFocus.get()),
    });
  }

  add({ id: "compose", group: "Commands", label: "New message", keys: ["c"], hint: "c", run: () => void composing.open({ mode: "new" }) });
  add({ id: "undo", group: "Commands", label: "Undo last action", keys: ["z"], hint: "z", run: m.undo });
  add({
    id: "brief", group: "Commands", label: "Today’s brief",
    run: () => {
      if (m.inInbox && !m.searching) m.showBrief();
      else go({ source: m.account ? { kind: "folder", account: m.account, folder: "INBOX" } : { kind: "allInboxes" }, opened: null });
    },
  });
  add({ id: "help", group: "Commands", label: "Keyboard help", keys: ["?"], hint: "?", run: () => helpOpen.set(true) });
  add({ id: "settings", group: "Commands", label: "Settings", keys: [","], hint: ",", run: m.openSettings });
  add({ id: "sync", group: "Commands", label: "Sync now", run: () => void syncNow(m.qc) });
  add({ id: "reconcile", group: "Commands", label: "Reconcile (prune ghosts)", run: () => void reconcile(m.qc) });
  add({ id: "commitments", group: "Commands", label: "Commitments", run: () => panel.set("commits") });
  add({ id: "review", group: "Commands", label: "Your week", run: () => panel.set("week") });
  add({ id: "docs", group: "Navigation", label: "Documents — find any attachment", run: () => docsOpen.set(true) });
  add({ id: "do", group: "Commands", label: "Do… — natural-language bulk action", run: () => doing.set({ state: "intro" }) });
  add({ id: "curate", group: "Commands", label: "Curate — rules and audit", run: () => curateOpen.set(true) });
  add({ id: "cleanup", group: "Commands", label: "Clean up senders (unsubscribe)", run: () => curateOpen.set(true) });
  for (const v of m.views) {
    const lane = v.id === "__lane_feed__" ? "feed" : v.id === "__lane_papertrail__" ? "papertrail" : undefined;
    add({ id: `view:${v.id}`, group: "Navigation", label: `View · ${v.name}${lane ? " (lane)" : ""}`, run: () => go({ source: { kind: "search", q: viewQuery(v), account: m.account ?? "", ...(lane ? { lane } : {}) }, opened: null }) });
  }
  add({ id: "grouping", group: "Commands", label: "Toggle conversations / messages", run: () => setGrouping(!m.grouped) });
  add({ id: "rail", group: "Commands", label: "Folders: icons ⇄ names", keys: ["["], hint: "[", run: ui.toggleRail });
  add({ id: "search", group: "Commands", label: "Search", keys: ["/"], hint: "/", run: () => document.querySelector<HTMLInputElement>("#search")?.focus() });
  if (m.searching) add({ id: "save-view", group: "Commands", label: "Save this search as a view", keys: ["v"], hint: "v", run: ui.saveSearch });
  for (const t of (["day", "dusk", "night"] as const).filter((x) => x !== theme.get())) {
    add({ id: `theme:${t}`, group: "Commands", label: `Theme → ${THEME_LABEL[t]}`, run: () => { setTheme(t); toast(`${THEME_LABEL[t]} theme`); } });
  }
  return out;
}

export function commandForKey(commands: Command[], key: string): Command | undefined {
  return commands.find((c) => c.keys?.includes(key));
}

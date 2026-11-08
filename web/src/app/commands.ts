// Everything you can do from here, in one list: the keys and the palette both read it, so a command means the same either
// way and neither can drift from the other.
import { panel } from "../features/assistant/Panels";
import { doing } from "../features/assistant/ask";
import { composing } from "../features/compose/compose";
import { curateOpen, filterForm, newFilter } from "../features/curation/open";
import { answer } from "../features/compose/draftAhead";
import * as actions from "../features/mailbox/actions";
import { summarize } from "../features/mailbox/AiCard";
import { viewQuery } from "../features/mailbox/FolderRail";
import { readFocus, setReadFocus } from "../features/mailbox/Reader";
import { roleLabel, VIRTUAL_TITLE } from "../features/mailbox/rows";
import { snoozeAsk } from "../features/attention/SnoozeMenu";
import { setGrouping, type Mailbox } from "../features/mailbox/useMailbox";
import { helpOpen, type Legend } from "../features/status/Help";
import { reconcile, syncNow } from "../features/status/Status";
import { copyOf, type Copy } from "../lib/copy";
import { shortAddress } from "../lib/text";
import { setTheme, theme, THEME_LABEL } from "../lib/theme";
import { toast } from "../lib/toast";
import { go, VIRTUAL_FOLDERS } from "./route";

export interface Command {
  id: string;
  group: "Navigation" | "Views" | "Actions" | "Commands";
  label: string;
  keys?: string[];     // what presses it, by KeyboardEvent.key with letters lowered
  hint?: string;       // what the palette shows for it
  legend?: string;     // what the keys legend says of it, where its label says less
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

// Where g then a letter goes, as the legend and the hint name it.
export const goKeys = (): Legend["go"] => [
  ...Object.entries(ROLE_KEYS).map(([key, where]) => ({ key, where })),
  ...Object.entries(VIRTUAL_KEYS).map(([key, name]) => ({ key, where: VIRTUAL_TITLE[name]!.toLowerCase() })),
];

// A saved view is saved already: nothing offers to save it again.
export const onSavedView = (m: Pick<Mailbox, "source" | "views">) => m.source?.kind === "search" && m.views.some((v) => viewQuery(v) === (m.source as { q: string }).q);

interface Ui { toggleRail: () => void; saveSearch: () => void; createFilter: () => void; openSearchBar: () => void }

// `whatever`: every command there is, whatever is open or marked now, for the legend, which never runs one.
export function buildCommands(m: Mailbox, ui: Ui, whatever = false): Command[] {
  const out: Command[] = [];
  const add = (c: Command) => out.push(c);
  const any = m.marks.size > 0;
  const on = whatever || m.openId !== null;
  const nameOf = (a: string) => m.aliases[a] || shortAddress(a);

  add({ id: "all-inboxes", group: "Navigation", label: "All inboxes", run: () => go({ source: { kind: "allInboxes" }, opened: null }) });
  add({ id: "commitments", group: "Navigation", label: "Commitments — your to-dos", run: () => panel.set("commits") });
  add({ id: "review", group: "Navigation", label: "Weekly review — how your week went", run: () => panel.set("week") });
  for (const name of VIRTUAL_FOLDERS) {
    add({ id: `across:${name}`, group: "Navigation", label: `All inboxes / ${VIRTUAL_TITLE[name]}`, run: () => go({ source: { kind: "across", folder: name }, opened: null }) });
  }
  for (const a of m.accounts) {
    add({ id: `acct:${a}`, group: "Navigation", label: `Account · ${nameOf(a)}`, run: () => go({ source: { kind: "folder", account: a, folder: "INBOX" }, opened: null }) });
    for (const f of m.everyFolder.get(a) ?? []) {
      add({ id: `fold:${a}:${f.name}`, group: "Navigation", label: `${nameOf(a)} / ${roleLabel(f)}`, run: () => go({ source: { kind: "folder", account: a, folder: f.name }, opened: null }) });
    }
    for (const name of ["__snoozed__", "__waiting__"]) {
      add({ id: `fold:${a}:${name}`, group: "Navigation", label: `${nameOf(a)} / ${VIRTUAL_TITLE[name]}`, run: () => go({ source: { kind: "folder", account: a, folder: name }, opened: null }) });
    }
  }

  add({ id: "move-down", group: "Actions", label: "Next message", keys: ["j", "ArrowDown"], hint: "j", run: () => m.move(1) });
  add({ id: "move-up", group: "Actions", label: "Previous message", legend: "Previous message (at the top: today’s brief)", keys: ["k", "ArrowUp"], hint: "k", run: () => m.move(-1) });
  add({ id: "next-unread", group: "Actions", label: "Next unread", keys: ["n"], hint: "n", run: () => { if (!m.moveUnread(1)) toast("No more unread below"); } });
  add({ id: "prev-unread", group: "Actions", label: "Previous unread", keys: ["p"], hint: "p", run: () => { if (!m.moveUnread(-1)) toast("No more unread above"); } });

  if (on || any) {
    add({ id: "archive", group: "Actions", label: "Archive", keys: ["e"], hint: "e", run: () => (any ? m.bulk("archive") : m.fileOpen("archive")) });
    add({ id: "trash", group: "Actions", label: "Delete", keys: ["d", "#", "Delete", "Backspace"], hint: "d", run: () => (any ? m.bulk("trash") : m.fileOpen("trash")) });
    add({ id: "junk", group: "Actions", label: "Mark junk", keys: ["b"], hint: "b", run: () => (any ? m.bulk("junk") : m.fileOpen("junk")) });
    add({ id: "move-to", group: "Actions", label: "Move to folder…", keys: ["m"], hint: "m", run: m.askMove });
    add({ id: "star", group: "Actions", label: "Star", keys: ["s"], hint: "s", run: () => (any ? m.bulk("star") : m.star()) });
    add({ id: "unread", group: "Actions", label: "Mark unread", keys: ["u"], hint: "u", run: () => (any ? m.bulk("unread") : m.unread()) });
    add({
      id: "snooze", group: "Actions", label: "Snooze…", legend: "Snooze (then 1–4)", keys: ["h"], hint: "h",
      run: () => {
        if (any) snoozeAsk.set({ target: { copies: m.markedRows.map(copyOf) }, rows: [...m.marks], anchor: document.querySelector("#bulkbar"), canUnsnooze: false });
        else {
          const target = m.target();
          if (target && m.openId !== null) snoozeAsk.set({ target, rows: [m.openId], anchor: document.querySelector('#reader [data-ca="snooze"],#reader [data-tca="snooze"]'), canUnsnooze: m.folder === "__snoozed__" });
        }
      },
    });
    add({
      id: "mute", group: "Actions", label: m.folder === "__muted__" ? "Unmute thread" : "Mute thread", keys: ["w"], hint: "w",
      run: () => (any ? m.bulk("mute") : m.folder === "__muted__" ? m.unmute() : m.mute()),
    });
  }
  if (on) {
    const replying = (run: (to: Copy) => unknown) => () => {
      const to = m.replyTo();
      if (to !== null) void run(to);
    };
    add({ id: "reply", group: "Actions", label: "Reply", keys: ["r"], hint: "r", run: replying(answer) });
    add({ id: "reply-all", group: "Actions", label: "Reply all", keys: ["a"], hint: "a", run: replying((to) => composing.reply(to, true)) });
    add({ id: "forward", group: "Actions", label: "Forward", keys: ["f"], hint: "f", run: replying(composing.forward) });
    add({
      id: "later", group: "Actions", label: m.folder === "__replylater__" ? "Reply later — Done" : "Reply later", keys: ["l"], hint: "l",
      run: () => (m.folder === "__replylater__" ? m.replyLaterDone() : m.replyLater()),
    });
    if (m.folder === "__waiting__") add({ id: "dismiss", group: "Actions", label: "Dismiss follow-up", run: m.dismissFollowup });
    add({ id: "summarize", group: "Actions", label: "AI summarize", keys: ["i"], hint: "i", run: () => { const t = m.target(); if (t) void summarize(t); } });
    add({ id: "mark", group: "Actions", label: "Select", keys: ["x"], hint: "x", run: () => actions.toggleMark(m.openId!) });
    add({ id: "read-settings", group: "Commands", label: "Reading settings — width, text, mail colors", run: () => void setTimeout(() => document.querySelector<HTMLElement>("#reader [data-readset]")?.click()) });
    add({
      id: "focus", group: "Commands", label: readFocus.get() ? "Leave focus reading" : "Focus reading — hide folders and list", keys: ["Enter"], hint: "↵",
      run: () => setReadFocus(!readFocus.get()),
    });
  }

  add({ id: "compose", group: "Commands", label: "Compose new message", keys: ["c"], hint: "c", run: () => void composing.open({ mode: "new" }) });
  add({ id: "undo", group: "Commands", label: "Undo last action", legend: "Undo last action (archive · delete · junk · snooze · mute)", keys: ["z"], hint: "z", run: m.undo });
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
  add({ id: "docs", group: "Navigation", label: "Documents — find any attachment", run: () => go({ source: { kind: "documents" }, opened: null }) });
  add({ id: "do", group: "Commands", label: "Do… — natural-language bulk action", run: () => doing.set({ state: "intro" }) });
  add({ id: "curate", group: "Commands", label: "Curate — rules and audit", run: () => curateOpen.set(true) });
  add({ id: "cleanup", group: "Commands", label: "Clean up senders (unsubscribe)", run: () => curateOpen.set(true) });
  add({ id: "new-filter", group: "Commands", label: "New filter — star or mark read new mail", run: () => filterForm.set(newFilter()) });
  for (const v of m.views) {
    const lane = v.id === "__lane_feed__" ? "feed" : v.id === "__lane_papertrail__" ? "papertrail" : undefined;
    add({ id: `view:${v.id}`, group: "Views", label: `${v.name}${lane ? " (lane)" : ""}`, run: () => go({ source: { kind: "search", q: viewQuery(v), account: m.account ?? "", ...(lane ? { lane } : {}) }, opened: null }) });
  }
  add({ id: "grouping", group: "Commands", label: "Toggle conversations / messages", run: () => setGrouping(!m.grouped) });
  add({ id: "rail", group: "Commands", label: "Folders: icons ⇄ names", legend: "Folders: icons ⇄ names · drag the list edge to resize", keys: ["["], hint: "[", run: ui.toggleRail });
  add({ id: "search", group: "Commands", label: "Search", legend: "Search · ?question asks your inbox · !command plans a bulk action", keys: ["/"], hint: "/", run: ui.openSearchBar });
  if (whatever || (m.searching && !onSavedView(m))) add({ id: "save-view", group: "Commands", label: "Save this search as a view", keys: ["v"], hint: "v", run: ui.saveSearch });
  if (whatever || (m.source?.kind === "search" && !m.source.lane && !m.source.thread)) add({ id: "create-filter", group: "Commands", label: "Create a filter from this search", run: ui.createFilter });
  for (const t of (["day", "dusk", "night"] as const).filter((x) => x !== theme.get())) {
    add({ id: `theme:${t}`, group: "Commands", label: `Theme → ${THEME_LABEL[t]}`, run: () => { setTheme(t); toast(`${THEME_LABEL[t]} theme`); } });
  }
  return out;
}

// What the keys legend shows: every command that has keys, and where g goes.
export const legendOf = (m: Mailbox, ui: Ui): Legend => ({ commands: buildCommands(m, ui, true).filter((c) => c.keys), go: goKeys() });

export function commandForKey(commands: Command[], key: string): Command | undefined {
  return commands.find((c) => c.keys?.includes(key));
}

// What a list shows, the same shape from a folder and from a search. Each list row says what the AI's verdict asks of
// you (`needsYou`); the page reads that and never works it out.
import type { GetAnswers } from "../../api/types.gen";

export type FolderRow = GetAnswers["/api/messages"]["items"][number];
export type SearchRow = NonNullable<GetAnswers["/api/search"]["items"]>[number];
export type FolderInfo = GetAnswers["/api/folders"]["folders"][number];

export interface Row {
  pk: number;
  key: number;
  account: string;
  folder: string;
  fromName: string;
  fromAddr: string;
  subject: string;
  snippet: string;
  when: string;
  ts: number | null;
  seen: boolean;
  flagged: boolean;
  hasAttach: boolean;
  isJunk: boolean;
  bucket: string;
  threadId: string;
  count: number;
  action: string;
  summary: string;
  needsYou: boolean;
}

export function fromFolder(it: FolderRow, account: string, folder: string): Row {
  return {
    pk: it.pk,
    key: it.key,
    account: it.account ?? account,
    folder: it.folder ?? folder,
    fromName: it.from_name,
    fromAddr: it.from_addr,
    subject: it.subject,
    snippet: it.snippet,
    when: it.waiting_label || it.when,
    ts: it.ts ?? null,
    seen: Boolean(it.seen),
    flagged: Boolean(it.flagged),
    hasAttach: Boolean(it.has_attach),
    isJunk: Boolean(it.is_junk),
    bucket: it.bucket,
    threadId: it.thread_id,
    count: it.count ?? 1,
    action: String(it.action ?? "").trim(),
    summary: it.summary ?? "",
    needsYou: Boolean(it.needs_you),
  };
}

export function fromSearch(it: SearchRow): Row {
  return {
    pk: it.pk,
    key: it.key,
    account: it.account,
    folder: it.folder,
    fromName: it.from_name,
    fromAddr: it.from_addr,
    subject: it.subject,
    snippet: it.snippet,
    when: it.when,
    ts: null,
    seen: Boolean(it.seen),
    flagged: Boolean(it.flagged),
    hasAttach: Boolean(it.has_attach),
    isJunk: Boolean(it.is_junk),
    bucket: it.bucket,
    threadId: "",
    count: 1,
    action: "",
    summary: "",
    needsYou: Boolean(it.needs_you),
  };
}

// A row that stands for a whole conversation: it opens in the conversation view, and files whole.
export const isConversation = (r: Row, grouped: boolean, folder = r.folder) =>
  grouped && r.count > 1 && Boolean(r.threadId) && !/draft/i.test(folder);

export type InboxTab = "needs" | "all" | "unread";

// The Inbox's tabs. A message you just opened stays put until you move on.
export function inTab(rows: Row[], tab: InboxTab, openPk: number | null): Row[] {
  if (tab === "needs") return rows.filter((r) => r.needsYou || r.pk === openPk);
  if (tab === "unread") return rows.filter((r) => !r.seen || r.pk === openPk);
  return rows;
}

// Day sections go only on real folders listed newest first; a virtual folder sorts by a clock of its own.
export const inDaySections = (rows: Row[], folder: string) =>
  !folder.startsWith("__") && rows.every((r, i) => r.ts !== null && (i === 0 || (rows[i - 1]!.ts ?? 0) >= r.ts));

export const VIRTUAL_TITLE: Record<string, string> = {
  __snoozed__: "Snoozed", __muted__: "Muted", __waiting__: "Waiting", __replylater__: "Reply later", __screener__: "Screener",
};
export const FOLDER_ICON: Record<string, string> = {
  inbox: "inbox", sent: "send", drafts: "file", archive: "archive", all: "archive", trash: "trash", junk: "ban", custom: "folder",
};

export function roleLabel(f: FolderInfo): string {
  if (f.role === "custom") return f.name;
  if (f.role === "all") return "All mail";
  return f.role.charAt(0).toUpperCase() + f.role.slice(1);
}

export function folderTitle(name: string, folders: FolderInfo[] | undefined): string {
  if (VIRTUAL_TITLE[name]) return VIRTUAL_TITLE[name]!;
  const known = folders?.find((f) => f.name === name);
  return known ? roleLabel(known) : name;
}

// How filing went, in words.
export function filedWords(verb: string, r: { count?: number; failed?: number; status?: string; queued?: unknown }, readOnly: boolean): string {
  const n = r.count || 1;
  const later = r.status === "queued" || r.queued ? (readOnly ? " · waits for a full run" : " · once the server is back") : "";
  return (r.failed ? `${verb} ${n} of ${n + r.failed} — the rest stayed` : n > 1 ? `${verb} ${n} messages` : verb) + later;
}

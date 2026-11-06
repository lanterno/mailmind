// The mailbox as the app's parts see it: where you are, what the list shows now, and each thing you can do from here.
// The reader's buttons, the keys and the command palette all go through this one set of verbs.
import { useQueryClient } from "@tanstack/react-query";
import { go, useRoute, type ListSource, type Opened, type Route } from "../../app/route";
import { createStore, store, stored } from "../../lib/store";
import * as actions from "./actions";
import { groupedPref, saveGroupedPref, useDigest, useFolders, useMessages } from "./data";
import { folderTitle, inTab, isConversation, type InboxTab, type Row } from "./rows";

function initialTab(): InboxTab {
  const saved = stored("mm_inbox_tab");
  return saved === "all" || saved === "unread" ? saved : "needs";
}
const inboxTab = createStore<InboxTab>(initialTab());
const grouping = createStore(groupedPref());

export function setInboxTab(tab: InboxTab) {
  inboxTab.set(tab);
  store("mm_inbox_tab", tab);
}

export function setGrouping(on: boolean) {
  saveGroupedPref(on);
  grouping.set(on);
}

export function useMailbox() {
  const qc = useQueryClient();
  const { source, opened } = useRoute();
  const digest = useDigest();
  const accounts = digest.data?.accounts ?? [];
  const aliases = digest.data?.aliases ?? {};
  const grouped = grouping.useValue();
  const tab = inboxTab.useValue();
  const list = useMessages(source, grouped, accounts);
  const hidden = actions.filing.useValue();
  const marks = actions.marked.useValue();

  const searching = source?.kind === "search";
  const allInboxes = source?.kind === "allInboxes";
  const folder = source?.kind === "folder" ? source.folder : allInboxes ? "INBOX" : "";
  const account = source?.kind === "folder" ? source.account : source?.kind === "search" && source.account ? source.account : allInboxes ? undefined : accounts[0];
  const { data: folderData } = useFolders(account);
  const inInbox = allInboxes || folder.toUpperCase() === "INBOX";

  const pages = list.data?.pages ?? [];
  const all = pages.flatMap((p) => p.rows).filter((r) => !hidden.has(r.pk));
  const openPk = opened?.pk ?? null;
  const rows = inInbox && !searching ? inTab(all, tab, openPk) : all;
  const openRow = openPk === null ? undefined : all.find((r) => r.pk === openPk);
  const markedRows = all.filter((r) => marks.has(r.pk));

  const isWhole = (r: Row) => !searching && isConversation(r, grouped, r.folder);
  const opening = (r: Row): Opened =>
    isWhole(r) ? { kind: "thread", id: r.threadId, pk: r.pk, account: r.account } : { kind: "message", pk: r.pk };
  const open = (r: Row) => go({ source, opened: opening(r) }, { replace: Boolean(opened) });
  const close = () => go({ source, opened: null });

  // Where to go once `pks` leave the list: on to the next, if what you were reading is among them.
  const afterLeaving = (pks: number[]): Route | null => {
    if (openPk === null || !pks.includes(openPk)) return null;
    const i = rows.findIndex((r) => r.pk === openPk);
    const left = rows.filter((r) => !pks.includes(r.pk) || r.pk === openPk);
    const at = left.findIndex((r) => r.pk === openPk);
    const next = i >= 0 ? (left[at + 1] ?? left[at - 1]) : undefined;
    return { source, opened: next ? opening(next) : null };
  };

  const move = (by: number) => {
    const i = rows.findIndex((r) => r.pk === openPk);
    const target = rows[i < 0 ? 0 : Math.max(0, Math.min(rows.length - 1, i + by))];
    if (target) open(target);
  };
  const moveUnread = (by: number) => {
    const i = rows.findIndex((r) => r.pk === openPk);
    for (let j = i + by; j >= 0 && j < rows.length; j += by) {
      if (!rows[j]!.seen) {
        open(rows[j]!);
        return true;
      }
    }
    return false;
  };

  // What a verb on the open thing is about. A conversation is named whole; a message by its pk.
  const target = (): actions.Target | null => {
    if (!opened) return null;
    if (opened.kind === "thread") return { account: opened.account, thread_id: opened.id };
    return { pk: opened.pk };
  };
  const muteTarget = (): actions.Target | null => {
    if (!opened) return null;
    if (opened.kind === "thread") return { account: opened.account, thread_id: opened.id };
    return { pk: opened.pk, thread_id: openRow?.threadId || null } as actions.Target;
  };

  const file = (r: Pick<Row, "pk">, kind: actions.Filing) => {
    const whole = opened?.kind === "thread" && opened.pk === r.pk ? true : "threadId" in r ? isWhole(r as Row) : false;
    void actions.fileRow(qc, kind, r, whole, afterLeaving([r.pk]));
  };
  const fileOpen = (kind: actions.Filing) => {
    if (openPk !== null) file(openRow ?? { pk: openPk }, kind);
  };
  const bulk = (verb: actions.BulkVerb) => void actions.bulk(qc, verb, markedRows, isWhole, afterLeaving(markedRows.map((r) => r.pk)));

  const onOpen = (run: (t: actions.Target, rows: number[], after: Route | null) => void, get = target) => {
    const t = get();
    if (t && openPk !== null) run(t, [openPk], afterLeaving([openPk]));
  };
  const nextAfterOpen = (): Route | null => {
    if (opened?.kind === "thread") return null;
    const i = rows.findIndex((r) => r.pk === openPk), next = rows[i + 1] ?? rows[i - 1];
    return i >= 0 && next && next.pk !== openPk ? { source, opened: opening(next) } : null;
  };

  return {
    qc,
    source,
    opened,
    accounts,
    aliases,
    account,
    digest: digest.data,
    grouped,
    tab,
    list,
    folder,
    folders: folderData,
    searching,
    allInboxes,
    inInbox,
    all,
    rows,
    openPk,
    openRow,
    marks,
    markedRows,
    title: allInboxes ? "All inboxes" : searching ? "Search" : folderTitle(folder, folderData?.folders),
    hasMore: Boolean(list.hasNextPage),
    isWhole,
    open,
    close,
    move,
    moveUnread,
    file,
    fileOpen,
    bulk,
    target,
    undo: () => void actions.undo(qc),
    star: () => {
      if (openRow) void actions.setStar(qc, openRow);
    },
    unread: () => {
      if (openPk !== null) void actions.markUnread(qc, openPk);
    },
    mute: () => onOpen((t, r, a) => void actions.mute(qc, t, r, a), muteTarget),
    unmute: () => onOpen((t, r, a) => void actions.unmute(qc, t, folder === "__muted__" ? r : [], folder === "__muted__" ? a : null), muteTarget),
    dismissFollowup: () => onOpen((t, r, a) => void actions.dismissFollowup(qc, t, r, a), muteTarget),
    replyLater: () => {
      const t = muteTarget();
      if (t) void actions.replyLater(qc, t, nextAfterOpen());
    },
    replyLaterDone: () => onOpen((t, r, a) => void actions.replyLaterDone(qc, t, r, a), muteTarget),
    snooze: (t: actions.Target, when: actions.Snoozing, pks: number[]) => {
      void actions.snooze(qc, t, when, pks, afterLeaving(pks));
      actions.clearMarks();
    },
    unsnooze: () => onOpen((t, r, a) => void actions.unsnooze(qc, t, r, a)),
    goToFolder: (name: string) => {
      const acct = account ?? accounts[0];
      if (acct) go({ source: { kind: "folder", account: acct, folder: name } as ListSource, opened: null });
    },
    search: (q: string) => go({ source: { kind: "search", q, account: account ?? "" }, opened: null }),
  };
}

export type Mailbox = ReturnType<typeof useMailbox>;

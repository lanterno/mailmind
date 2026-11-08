// The mailbox as the app's parts see it: where you are, what the list shows now, and each thing you can do from here.
// The reader's buttons, the keys and the command palette all go through this one set of verbs.
import { useEffect, useEffectEvent, useRef } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { get } from "../../lib/api/client";
import type { AnyOf, GetAnswers } from "../../lib/api/types";
import { go, useRoute, type ListSource, type Opened, type Route } from "../../app/route";
import { createStore, store, stored } from "../../lib/store";
import { copyId, copyOf, copyQuery, eachCopyOnce, sameCopy, type Copy } from "../../lib/copy";
import { settingsOpen } from "../settings";
import * as actions from "./actions";
import { groupedPref, saveGroupedPref, useDigest, useEveryAccountsFolders, useFolders, useMessages, useViews } from "./data";
import { folderTitle, inTab, isConversation, type InboxTab, type Row } from "./rows";

function initialTab(): InboxTab {
  const saved = stored("mm_inbox_tab");
  return saved === "all" || saved === "unread" ? saved : "needs";
}
const DIGEST_TITLE = { needs: "Needs you", tidy: "Safe to file" };
const LANE_TITLE = { feed: "Feed", papertrail: "Paper Trail" };
const inboxTab = createStore<InboxTab>(initialTab());
const prefetched = new Set<string>();
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
  const { source, opened, brief } = useRoute();
  const digest = useDigest();
  const accounts = digest.data?.accounts ?? [];
  const aliases = digest.data?.aliases ?? {};
  const grouped = grouping.useValue();
  const tab = inboxTab.useValue();
  const list = useMessages(source, grouped);
  const hidden = actions.filing.useValue();
  const marks = actions.marked.useValue();

  const searching = source?.kind === "search";
  const allInboxes = source?.kind === "allInboxes";
  const folder = source?.kind === "folder" ? source.folder : allInboxes ? "INBOX" : "";
  const account = source?.kind === "folder" ? source.account : source?.kind === "search" && source.account ? source.account : allInboxes ? undefined : accounts[0];
  const { data: folderData } = useFolders(account);
  const everyFolder = useEveryAccountsFolders(accounts);
  const inInbox = allInboxes || folder.toUpperCase() === "INBOX";

  const pages = list.data?.pages ?? [];
  const all = eachCopyOnce(pages.flatMap((p) => p.rows)).filter((r) => !hidden.has(r.id));
  const openCopy: Copy | null = opened ? copyOf(opened) : null;
  const openId = openCopy ? copyId(openCopy) : null;
  const rows = inInbox && !searching ? inTab(all, tab, openId) : all;
  const openRow = openId === null ? undefined : all.find((r) => r.id === openId);
  const markedRows = all.filter((r) => marks.has(r.id));
  const views = useViews().data?.views ?? [];

  // The next two rows' bodies, so moving down shows them at once. The server skips one it would have to wait for.
  const at = rows.findIndex((r) => r.id === openId);
  const nextTwo = openId === null || at < 0 ? [] : rows.slice(at + 1, at + 3);
  const nextTwoNow = nextTwo.map((r) => r.id).join("\n");   // by value: the array itself is new each render
  const prefetchNext = useEffectEvent(() => {
    for (const r of nextTwo) {
      if (prefetched.has(r.id)) continue;
      prefetched.add(r.id);
      get(`/api/message/body?${copyQuery(r)}&prefetch=1`).catch(() => prefetched.delete(r.id));
    }
  });
  useEffect(() => prefetchNext(), [nextTwoNow]);

  const isWhole = (r: Row) => !searching && isConversation(r, grouped, r.folder);
  const opening = (r: Row): Opened =>
    isWhole(r) ? { kind: "thread", id: r.threadId, account: r.account, ...copyOf(r) } : { kind: "message", ...copyOf(r) };
  const open = (r: Row) => go({ source, opened: opening(r) }, { replace: Boolean(opened) });
  const close = () => go({ source, opened: null });
  // A copy named from elsewhere (the brief, a to-do): a conversation in this list opens as one, anything else as the message.
  const openAny = (copy: Copy) => {
    const r = all.find((x) => x.id === copyId(copy));
    if (r) open(r);
    else go({ source, opened: { kind: "message", ...copyOf(copy) } }, { replace: Boolean(opened) });
  };
  const openNamed = (copy: Copy, account: string, thread: string | null | undefined) =>
    go({ source, opened: thread ? { kind: "thread", id: thread, account, ...copyOf(copy) } : { kind: "message", ...copyOf(copy) } });

  // A draft saved again is a new copy, and one discarded is gone: don't stay on a copy the list no longer has.
  const inDrafts = folderData?.folders?.some((f) => f.name === folder && f.role === "drafts") ?? false;
  const settled = list.isSuccess && !list.isFetching;
  const copyGone = inDrafts && settled && opened?.kind === "message" && !all.some((r) => sameCopy(r, opened));
  const moveOffGoneCopy = useEffectEvent(() => go({ source, opened: rows[0] ? opening(rows[0]) : null }, { replace: true }));
  useEffect(() => {
    if (copyGone) moveOffGoneCopy();   // once, as it goes: a list refreshing after that must not send you anywhere
  }, [copyGone]);

  // On a desktop a folder opens on its first message, as the Inbox opens on the brief. A phone stays on the list. Once per
  // list, so a message you closed stays closed, and not over a message already open.
  const sourceNow = JSON.stringify(source), hasFirst = Boolean(rows[0]);
  const stillReading = Boolean(pages[pages.length - 1]?.syncing);
  const openFirst = useEffectEvent(() => {
    if (opened || source?.kind !== "folder" || inInbox || source.folder === "__screener__" || !rows[0]) return;
    if (!matchMedia("(max-width:820px)").matches) go({ source, opened: opening(rows[0]) }, { replace: true });
  });
  const openedFirstOf = useRef<string | null>(null);
  useEffect(() => {
    if (!list.isSuccess || openedFirstOf.current === sourceNow || (!hasFirst && stillReading)) return;
    openedFirstOf.current = sourceNow;
    openFirst();
  }, [list.isSuccess, sourceNow, hasFirst, stillReading]);

  // Where to go once the copies `ids` leave the list: on to the next, if what you were reading is among them.
  const afterLeaving = (ids: string[]): Route | null => {
    if (openId === null || !ids.includes(openId)) return null;
    const i = rows.findIndex((r) => r.id === openId);
    const left = rows.filter((r) => !ids.includes(r.id) || r.id === openId);
    const at = left.findIndex((r) => r.id === openId);
    const next = i >= 0 ? (left[at + 1] ?? left[at - 1]) : undefined;
    return { source, opened: next ? opening(next) : null };
  };

  const showBrief = () => go({ source, opened: null, brief: true });
  const move = (by: number) => {
    const i = rows.findIndex((r) => r.id === openId);
    if (by < 0 && i === 0 && inInbox && !searching) return showBrief();     // above the first row is the brief
    const target = rows[i < 0 ? 0 : Math.max(0, Math.min(rows.length - 1, i + by))];
    if (target) open(target);
  };
  const moveUnread = (by: number) => {
    const i = rows.findIndex((r) => r.id === openId);
    for (let j = i + by; j >= 0 && j < rows.length; j += by) {
      if (!rows[j]!.seen) {
        open(rows[j]!);
        return true;
      }
    }
    return false;
  };

  // What a verb on the open thing is about. A conversation is named whole; a message by its copy.
  const target = (): actions.Target | null => {
    if (!opened) return null;
    if (opened.kind === "thread") return { account: opened.account, thread_id: opened.id };
    return copyOf(opened);
  };
  const muteTarget = (): actions.Target | null => {
    if (!opened) return null;
    if (opened.kind === "thread") return { account: opened.account, thread_id: opened.id };
    return { ...copyOf(opened), thread_id: openRow?.threadId || null };
  };

  // What a reply answers: the message, or the conversation's latest that isn't yours, as the copy the list or the thread has.
  const replyTo = (): Copy | null => {
    if (opened?.kind !== "thread") return openCopy;
    const t = qc.getQueryData<AnyOf<GetAnswers["/api/thread"]>>(["thread", opened.account, opened.id]);
    const latest = t?.items.find((x) => x.key === t.reply_key);
    return latest ? copyOf(latest) : null;
  };

  const file = (r: Copy & Partial<Row>, kind: actions.Filing, from?: "card") => {
    const whole = opened?.kind === "thread" && sameCopy(opened, r) ? true : "threadId" in r ? isWhole(r as Row) : false;
    void actions.fileRow(qc, kind, copyOf(r), whole, afterLeaving([copyId(r)]), from);
  };
  const fileOpen = (kind: actions.Filing) => {
    if (openCopy !== null) file(openRow ?? openCopy, kind);
  };
  const bulk = (verb: actions.BulkVerb) => void actions.bulk(qc, verb, markedRows, isWhole, afterLeaving(markedRows.map((r) => r.id)));

  const onOpen = (run: (t: actions.Target, rows: string[], after: Route | null) => void, get = target) => {
    const t = get();
    if (t && openId !== null) run(t, [openId], afterLeaving([openId]));
  };
  const nextAfterOpen = (): Route | null => {
    if (opened?.kind === "thread") return null;
    const i = rows.findIndex((r) => r.id === openId), next = rows[i + 1] ?? rows[i - 1];
    return i >= 0 && next && next.id !== openId ? { source, opened: opening(next) } : null;
  };

  return {
    qc,
    source,
    opened,
    briefAsked: Boolean(brief),
    accounts,
    aliases,
    account,
    digest: digest.data,
    digestFailed: digest.isLoadingError,
    retryDigest: () => void digest.refetch(),
    grouped,
    tab,
    list,
    folder,
    folders: folderData,
    everyFolder,
    searching,
    allInboxes,
    inInbox,
    all,
    rows,
    views,
    openCopy,
    openId,
    openRow,
    marks,
    markedRows,
    title: allInboxes ? "Inbox" : source?.kind === "search" ? (source.lane ? LANE_TITLE[source.lane] : "Search") : source?.kind === "digest" ? (source.list === "topic" ? source.label : DIGEST_TITLE[source.list]) : folderTitle(folder, folderData?.folders),
    hasMore: Boolean(list.hasNextPage),
    isWhole,
    open,
    openAny,
    openNamed,
    showBrief,
    openSettings: () => settingsOpen.set(true),
    close,
    move,
    moveUnread,
    file,
    fileOpen,
    bulk,
    target,
    replyTo,
    undo: () => void actions.undo(qc),
    star: () => {
      if (openRow) void actions.setStar(qc, openRow);
    },
    unread: () => {
      if (openCopy !== null) void actions.markUnread(qc, openCopy);
    },
    mute: () => onOpen((t, r, a) => void actions.mute(qc, t, r, a), muteTarget),
    unmute: () => onOpen((t, r, a) => void actions.unmute(qc, t, folder === "__muted__" ? r : [], folder === "__muted__" ? a : null), muteTarget),
    dismissFollowup: () => onOpen((t, r, a) => void actions.dismissFollowup(qc, t, r, a), muteTarget),
    replyLater: () => {
      const t = muteTarget();
      if (t) void actions.replyLater(qc, t, nextAfterOpen());
    },
    replyLaterDone: () => onOpen((t, r, a) => void actions.replyLaterDone(qc, t, r, a), muteTarget),
    snooze: (t: actions.Target, when: actions.Snoozing, ids: string[]) => {
      void actions.snooze(qc, t, when, ids, afterLeaving(ids));
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

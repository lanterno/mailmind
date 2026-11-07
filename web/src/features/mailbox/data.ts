// What the mailbox reads, and when it reads it again. The event stream says what changed (app/realtime.ts); a query
// refetches in place, so the row you are on and what you were reading stay put.
import { useInfiniteQuery, useQuery, type InfiniteData } from "@tanstack/react-query";
import { get } from "../../lib/api/client";
import { sourceKey, type ListSource } from "../../app/route";
import { store, stored } from "../../lib/store";
import { fromDigest, needsYou, safeToFile, topicItems, type DigestItem } from "../assistant";
import { fromFolder, fromSearch, type Row } from "./rows";

const PAGE = 200, ALL_INBOXES_EACH = 40;
const q = encodeURIComponent;

export interface ListPage { rows: Row[]; hasMore: boolean; total: number; syncing: boolean; syncFailed: boolean; catchingUp: boolean; error?: string }

export const useDigest = () => useQuery({ queryKey: ["digest"], queryFn: () => get("/api/digest?min=1&account=") });

export const useFolders = (account: string | undefined) =>
  useQuery({ queryKey: ["folders", account], queryFn: () => get(`/api/folders?account=${q(account!)}`), enabled: Boolean(account) });

async function folderPage(account: string, folder: string, grouped: boolean, offset: number): Promise<ListPage> {
  const group = grouped ? "&group=thread" : "";
  const d = await get(`/api/messages?account=${q(account)}&folder=${q(folder)}&limit=${PAGE}&offset=${offset}${group}`);
  return {
    rows: d.items.map((it) => fromFolder(it, account, folder)),
    hasMore: d.has_more,
    total: d.total,
    syncing: Boolean(d.syncing),
    syncFailed: Boolean(d.sync_failed),
    catchingUp: Boolean(d.catching_up),
  };
}

async function searchPage(query: string, account: string, offset: number): Promise<ListPage> {
  const d = await get(`/api/search?q=${q(query)}&scope=all&account=${q(account)}&limit=${PAGE}&offset=${offset}`);
  return { rows: (d.items ?? []).map(fromSearch), hasMore: Boolean(d.has_more), total: d.total ?? 0, syncing: false, syncFailed: false, catchingUp: false, ...(d.error ? { error: d.error } : {}) };
}

// One page of each account's Inbox, newest first. An account that can't answer leaves the rest in place.
async function allInboxesPage(accounts: string[], grouped: boolean): Promise<ListPage> {
  const group = grouped ? "&group=thread" : "";
  const pages = await Promise.all(accounts.map((a) =>
    get(`/api/messages?account=${q(a)}&folder=INBOX&limit=${ALL_INBOXES_EACH}${group}`)
      .then((d) => d.items.map((it) => fromFolder(it, a, "INBOX")))
      .catch(() => [] as Row[])));
  const rows = pages.flat().sort((x, y) => (y.ts ?? 0) - (x.ts ?? 0));
  return { rows, hasMore: false, total: rows.length, syncing: false, syncFailed: false, catchingUp: false };
}

// What the digest answers, as a list. Nothing here asks the AI: a topic comes from the brief as it is stored.
async function digestPage(source: Extract<ListSource, { kind: "digest" }>): Promise<ListPage> {
  const { items } = await get("/api/digest?min=1&account=");
  let chosen: DigestItem[];
  if (source.list === "topic") {
    const groups = (await get("/api/briefing")).groups ?? [];
    chosen = topicItems(groups.find((g) => g.label === source.label)?.pks ?? [], items);
  } else chosen = source.list === "needs" ? needsYou(items) : safeToFile(items);
  const rows = chosen.map(fromDigest);
  return { rows, hasMore: false, total: rows.length, syncing: false, syncFailed: false, catchingUp: false };
}

export function useMessages(source: ListSource | null, grouped: boolean, accounts: string[]) {
  return useInfiniteQuery({
    queryKey: ["messages", ...sourceKey(source), source?.kind === "folder" || source?.kind === "allInboxes" ? grouped : false],
    enabled: Boolean(source) && (source?.kind !== "allInboxes" || accounts.length > 0) && !(source?.kind === "folder" && source.folder === "__screener__"),
    initialPageParam: 0,
    queryFn: ({ pageParam }) => {
      if (source?.kind === "folder") return folderPage(source.account, source.folder, grouped, pageParam);
      if (source?.kind === "search") return searchPage(source.q, source.account, pageParam);
      if (source?.kind === "digest") return digestPage(source);
      return allInboxesPage(accounts, grouped);
    },
    getNextPageParam: (last, pages) => (last.hasMore ? pages.reduce((n, p) => n + p.rows.length, 0) : undefined),
  });
}

export type ListData = InfiniteData<ListPage, number>;

export const useBody = (pk: number | null) =>
  useQuery({
    queryKey: ["body", pk],
    queryFn: () => get(`/api/message/${pk!}/body`),
    enabled: pk !== null,
    staleTime: 60_000,
    refetchOnWindowFocus: false,
  });

export const useThread = (account: string | undefined, threadId: string | undefined) =>
  useQuery({
    queryKey: ["thread", account, threadId],
    queryFn: () => get(`/api/thread?account=${q(account!)}&thread_id=${q(threadId!)}`),
    enabled: Boolean(account && threadId),
  });

// Conversations on or off, kept under the key the current page uses.
export const groupedPref = () => stored("mm_threadview") !== "0";
export const saveGroupedPref = (on: boolean) => store("mm_threadview", on ? "1" : "0");

export const useViews = () => useQuery({ queryKey: ["views"], queryFn: () => get("/api/views") });

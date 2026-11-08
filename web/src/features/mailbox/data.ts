// What the mailbox reads, and when it reads it again. The event stream says what changed (app/realtime.ts); a query
// refetches in place, so the row you are on and what you were reading stay put.
import { useInfiniteQuery, useQuery, type InfiniteData, useQueries } from "@tanstack/react-query";
import { get, refusalOf } from "../../lib/api/client";
import type { AnyOf, GetAnswers } from "../../lib/api/types";
import { sourceKey, type ListSource } from "../../app/route";
import { createStore, store, stored } from "../../lib/store";
import { fromDigest, needsYou, safeToFile, topicItems, type DigestItem } from "../assistant";
import { fromFolder, fromSearch, type Row } from "./rows";

const PAGE = 200;
const q = encodeURIComponent;

export interface ListPage { rows: Row[]; hasMore: boolean; total: number; syncing: boolean; syncFailed: boolean; catchingUp: boolean; error?: string }

export const useDigest = () => useQuery({
  queryKey: ["digest"],
  queryFn: () => get("/api/digest"),
  refetchInterval: (q) => (q.state.status === "error" ? 5000 : false),     // the first read failed: the server may be starting
});

export const useFolders = (account: string | undefined) =>
  useQuery({ queryKey: ["folders", account], queryFn: () => get(`/api/folders?account=${q(account!)}`), enabled: Boolean(account) });

async function folderPage(account: string, folder: string, grouped: boolean, offset: number): Promise<ListPage> {
  const group = grouped ? "&group=thread" : "";
  const d: AnyOf<GetAnswers["/api/messages"]> = await get(`/api/messages?account=${q(account)}&folder=${q(folder)}&limit=${PAGE}&offset=${offset}${group}`);
  return {
    rows: d.items.map((it) => fromFolder(it, account, folder)),
    hasMore: d.has_more,
    total: d.total,
    syncing: Boolean(d.syncing),
    syncFailed: Boolean(d.sync_failed),
    catchingUp: Boolean(d.catching_up),
  };
}

// Search all your accounts or the one you're on, kept across reloads. With no account in mind, all of them.
export type SearchScope = "all" | "account";
export const searchScope = createStore<SearchScope>(stored("mm_searchscope") === "account" ? "account" : "all");
export function setSearchScope(s: SearchScope) {
  searchScope.set(s);
  store("mm_searchscope", s);
}

// A query the index can't read is refused in words of its own, which the list shows where its results would be.
async function searchPage(query: string, account: string, offset: number, scope: SearchScope): Promise<ListPage> {
  let d: AnyOf<GetAnswers["/api/search"]>;
  try {
    d = await get(`/api/search?q=${q(query)}&scope=${account ? scope : "all"}&account=${q(account)}&limit=${PAGE}&offset=${offset}`);
  } catch (e) {
    const no = refusalOf(e);
    if (!no) throw e;
    return { rows: [], hasMore: false, total: 0, syncing: false, syncFailed: false, catchingUp: false, ...(no.detail ? { error: no.detail } : {}) };
  }
  return { rows: (d.items ?? []).map(fromSearch), hasMore: Boolean(d.has_more), total: d.total ?? 0, syncing: false, syncFailed: false, catchingUp: false, ...(d.error ? { error: d.error } : {}) };
}

// Every account's Inbox, newest first, from what is stored: an account that's away leaves the rest in place, and the sync
// pill and the account alert speak for it.
async function allInboxesPage(grouped: boolean, offset: number): Promise<ListPage> {
  const group = grouped ? "&group=thread" : "";
  const d = await get(`/api/inboxes?limit=${PAGE}&offset=${offset}${group}`);
  return {
    rows: d.items.map((it) => fromFolder(it, it.account, "INBOX")),
    hasMore: d.has_more,
    total: d.total,
    syncing: Boolean(d.syncing),
    syncFailed: false,
    catchingUp: false,
  };
}

// What the digest answers, as a list. Nothing here asks the AI: a topic comes from the brief as it is stored.
async function digestPage(source: Extract<ListSource, { kind: "digest" }>): Promise<ListPage> {
  const { items } = await get("/api/digest");
  let chosen: DigestItem[];
  if (source.list === "topic") {
    const groups = (await get("/api/briefing")).groups ?? [];
    chosen = topicItems(groups.find((g) => g.label === source.label)?.pks ?? [], items);
  } else chosen = source.list === "needs" ? needsYou(items) : safeToFile(items);
  const rows = chosen.map(fromDigest);
  return { rows, hasMore: false, total: rows.length, syncing: false, syncFailed: false, catchingUp: false };
}

export function useMessages(source: ListSource | null, grouped: boolean) {
  const scope = searchScope.useValue();
  return useInfiniteQuery({
    queryKey: ["messages", ...sourceKey(source), source?.kind === "folder" || source?.kind === "allInboxes" ? grouped : source?.kind === "search" ? scope : false],
    enabled: Boolean(source) && !(source?.kind === "folder" && source.folder === "__screener__"),
    initialPageParam: 0,
    queryFn: ({ pageParam }) => {
      if (source?.kind === "folder") return folderPage(source.account, source.folder, grouped, pageParam);
      if (source?.kind === "search") return searchPage(source.q, source.account, pageParam, scope);
      if (source?.kind === "digest") return digestPage(source);
      return allInboxesPage(grouped, pageParam);
    },
    getNextPageParam: (last, pages) => (last.hasMore ? pages.reduce((n, p) => n + p.rows.length, 0) : undefined),
  });
}

export type ListData = InfiniteData<ListPage, number>;

// Every account's folders, so the palette reaches them all.
export function useEveryAccountsFolders(accounts: string[]) {
  const answers = useQueries({ queries: accounts.map((a) => ({ queryKey: ["folders", a], queryFn: () => get(`/api/folders?account=${q(a)}`) })) });
  return new Map(accounts.map((a, i) => [a, answers[i]?.data?.folders ?? []]));
}

// A body as the reader opens it. Only a `prefetch` answers `prefetched`, and the reader never asks for one.
export type Opened = Exclude<GetAnswers["/api/message/{pk}/body"], { prefetched: true }>;

export const useBody = (pk: number | null) =>
  useQuery({
    queryKey: ["body", pk],
    queryFn: () => get(`/api/message/${pk!}/body`) as Promise<Opened>,
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

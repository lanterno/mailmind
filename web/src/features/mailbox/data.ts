// What the mailbox reads, and when it reads it again. The event stream says what changed (app/realtime.ts); a query
// refetches in place, so the row you are on and what you were reading stay put.
import { useInfiniteQuery, useQuery, type InfiniteData, useQueries } from "@tanstack/react-query";
import { get, refusalOf } from "../../lib/api/client";
import type { AnyOf, GetAnswers, SearchScope } from "../../lib/api/types";
import { sourceKey, type ListSource } from "../../app/route";
import { pref } from "../../lib/prefs";
import { copyId, copyQuery, type Copy } from "../../lib/copy";
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

// Snoozed, Muted, Waiting and Reply later counted over every account, for the rail on All inboxes.
export const useFoldersAcross = (wanted: boolean) =>
  useQuery({ queryKey: ["folders", "__across__"], queryFn: () => get("/api/folders/across"), enabled: wanted });

// `account` is "" for a virtual folder over every account: each row names its own.
async function folderPage(account: string, folder: string, grouped: boolean, offset: number): Promise<ListPage> {
  const group = grouped ? "&group=thread" : "";
  const whose = account ? `account=${q(account)}&` : "";
  const d: AnyOf<GetAnswers["/api/messages"]> = await get(`/api/messages?${whose}folder=${q(folder)}&limit=${PAGE}&offset=${offset}${group}`);
  return {
    rows: d.items.map((it) => fromFolder(it)),
    hasMore: d.has_more,
    total: d.total,
    syncing: Boolean(d.syncing),
    syncFailed: Boolean(d.sync_failed),
    catchingUp: Boolean(d.catching_up),
  };
}

// Search all your accounts or the one you're on, kept across reloads. With no account in mind, all of them.
export type { SearchScope };
export const searchScope = pref("search_scope");
export const setSearchScope = (s: SearchScope) => searchScope.set(s);

// A query the index can't read is refused in words of its own, which the list shows where its results would be.
async function searchPage(query: string, account: string, offset: number, scope: SearchScope, thread: string | undefined): Promise<ListPage> {
  let d: AnyOf<GetAnswers["/api/search"]>;
  try {
    const where = thread ? `scope=account&account=${q(account)}&thread=${q(thread)}` : `scope=${account ? scope : "all"}&account=${q(account)}`;
    d = await get(`/api/search?q=${q(query)}&${where}&limit=${PAGE}&offset=${offset}`);
  } catch (e) {
    const no = refusalOf(e);
    if (!no) throw e;
    return { rows: [], hasMore: false, total: 0, syncing: false, syncFailed: false, catchingUp: false, ...(no.detail ? { error: no.detail } : {}) };
  }
  return { rows: (d.items ?? []).map(fromSearch), hasMore: Boolean(d.has_more), total: d.total ?? 0, syncing: false, syncFailed: false, catchingUp: false };
}

// Every account's Inbox, newest first, from what is stored: an account that's away leaves the rest in place, and the sync
// pill and the account alert speak for it.
async function allInboxesPage(grouped: boolean, offset: number): Promise<ListPage> {
  const group = grouped ? "&group=thread" : "";
  const d = await get(`/api/inboxes?limit=${PAGE}&offset=${offset}${group}`);
  return {
    rows: d.items.map((it) => fromFolder(it)),
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
    chosen = topicItems(groups.find((g) => g.label === source.label)?.messages ?? [], items);
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
      if (source?.kind === "across") return folderPage("", source.folder, grouped, pageParam);
      if (source?.kind === "search") return searchPage(source.q, source.account, pageParam, scope, source.thread);
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

// A body as the reader opens it, by the copy it is. Only a `prefetch` answers `prefetched`, and the reader never asks for one.
export type Opened = Exclude<GetAnswers["/api/message/body"], { prefetched: boolean }>;

const warmed = new Set<string>();

// A body made ready on the server for when it opens, once a page. The server skips one it would have to wait for, and that
// one may be asked for again.
export function warmBody(copy: Copy): Promise<unknown> {
  const id = copyId(copy);
  if (warmed.has(id)) return Promise.resolve();
  warmed.add(id);
  return (get(`/api/message/body?${copyQuery(copy)}&prefetch=1`) as Promise<{ prefetched: boolean }>).then(
    (a) => { if (!a.prefetched) warmed.delete(id); },
    () => warmed.delete(id),
  );
}

const WARM_MEMBERS = 12;

// The members of a conversation a person might open next. The newest first, as a thread reads up from its end, and one at a
// time: the server skips a body it would have to wait for, so asked all at once it would warm one.
export async function warmMembers(members: Copy[], wanted: () => boolean) {
  for (const copy of members.slice(-WARM_MEMBERS).reverse()) {
    if (!wanted()) return;
    await warmBody(copy);
  }
}

export const useBody = (copy: Copy | null) =>
  useQuery({
    queryKey: ["body", copy?.key, copy?.folder, copy?.uid],
    queryFn: () => get(`/api/message/body?${copyQuery(copy!)}`) as Promise<Opened>,
    enabled: copy !== null,
    staleTime: 60_000,
    refetchOnWindowFocus: false,
  });

export const useThread = (account: string | undefined, threadId: string | undefined) =>
  useQuery({
    queryKey: ["thread", account, threadId],
    queryFn: () => get(`/api/thread?account=${q(account!)}&thread_id=${q(threadId!)}`),
    enabled: Boolean(account && threadId),
  });

export const useViews = () => useQuery({ queryKey: ["views"], queryFn: () => get("/api/views") });

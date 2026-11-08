// Where you are lives in the URL, so a deep link, Back and a reload all come back to it. What the list shows is one
// type, not a set of flags: a folder (a real one, or a virtual one named __like_this__), all inboxes, or a search.
import { useSyncExternalStore } from "react";
import { navigate } from "../lib/backTrap";
import type { Copy } from "../lib/copy";

export type ListSource =
  | { kind: "folder"; account: string; folder: string }
  | { kind: "allInboxes" }
  | { kind: "search"; q: string; account: string; lane?: Lane; thread?: string }     // thread: only this conversation of the account
  | ({ kind: "digest" } & DigestList);

// Lists the digest answers rather than a folder holds: what needs you, what is safe to file, a topic from the brief.
export type DigestList = { list: "needs" | "tidy" } | { list: "topic"; label: string };

// Two built-in views read better as something other than rows. The query is the same either way.
export type Lane = "feed" | "papertrail";

// What is open names a copy, by its key, folder and uid: a link kept from before a move or a renumber opens the copy it
// named, or nothing, and never another message or another copy beside it. A conversation names the copy it opened on, and
// the thread.
export type Opened = ({ kind: "message" } & Copy) | ({ kind: "thread"; id: string; account: string } & Copy) | null;

// `brief` is the phone's way to say the brief is showing: there a pane is either the list or what's open.
export interface Route { source: ListSource | null; opened: Opened; brief?: boolean }

const seg = encodeURIComponent;

export function pathOf({ source, opened, brief }: Route): string {
  let path = "";
  const query = new URLSearchParams();
  if (source?.kind === "folder") path += `/m/${seg(source.account)}/${seg(source.folder)}`;
  else if (source?.kind === "allInboxes") path += "/all";
  else if (source?.kind === "search") {
    path += "/search";
    query.set("q", source.q);
    if (source.account) query.set("account", source.account);
    if (source.lane) query.set("lane", source.lane);
    if (source.thread) query.set("thread", source.thread);
  }
  else if (source?.kind === "digest") path += source.list === "topic" ? `/brief/topic/${seg(source.label)}` : `/brief/${source.list}`;
  if (brief) query.set("brief", "1");
  if (opened?.kind === "thread") {
    query.set("c", opened.id);
    query.set("ca", opened.account);
  }
  if (opened) {
    query.set("key", String(opened.key));
    query.set("folder", opened.folder);
    query.set("uid", String(opened.uid));
  }
  const q = query.toString();
  return `${path || "/"}${q ? `?${q}` : ""}`;
}

export function parseRoute(pathname: string, search: string): Route {
  const q = new URLSearchParams(search);
  const parts = pathname.split("/").filter(Boolean).map(decodeURIComponent);
  let source: ListSource | null = null;
  if (parts[0] === "m" && parts[1] && parts[2]) source = { kind: "folder", account: parts[1], folder: parts[2] };
  else if (parts[0] === "all") source = { kind: "allInboxes" };
  else if (parts[0] === "search") {
    const lane = q.get("lane");
    const thread = q.get("thread");
    source = { kind: "search", q: q.get("q") ?? "", account: q.get("account") ?? "", ...(lane === "feed" || lane === "papertrail" ? { lane } : {}), ...(thread ? { thread } : {}) };
  }
  else if (parts[0] === "brief" && (parts[1] === "needs" || parts[1] === "tidy")) source = { kind: "digest", list: parts[1] };
  else if (parts[0] === "brief" && parts[1] === "topic" && parts[2]) source = { kind: "digest", list: "topic", label: parts[2] };
  const key = Number(q.get("key")), folder = q.get("folder"), uid = Number(q.get("uid"));
  const whole = (n: number, text: string | null) => /^[1-9]\d*$/.test(text ?? "") && Number.isSafeInteger(n);
  let opened: Opened = null;
  if (whole(key, q.get("key")) && whole(uid, q.get("uid")) && folder) {
    const thread = q.get("c");
    opened = thread ? { kind: "thread", id: thread, account: q.get("ca") ?? "", key, folder, uid } : { kind: "message", key, folder, uid };
  }
  return q.get("brief") === "1" ? { source, opened, brief: true } : { source, opened };
}

const listeners = new Set<() => void>();
let current = "";
const read = () => location.pathname + location.search;

function subscribe(fn: () => void) {
  listeners.add(fn);
  window.addEventListener("popstate", fn);
  return () => {
    listeners.delete(fn);
    window.removeEventListener("popstate", fn);
  };
}

export function go(route: Route, { replace = false } = {}) {
  const path = pathOf(route);
  navigate((overTrap) => {
    if (path === read()) return;
    if (replace || overTrap) history.replaceState(null, "", path);
    else history.pushState(null, "", path);
    listeners.forEach((fn) => fn());
  });
}

let cached: Route = { source: null, opened: null };
function snapshot(): Route {
  const now = read();
  if (now !== current) {
    current = now;
    cached = parseRoute(location.pathname, location.search);
  }
  return cached;
}

export const useRoute = () => useSyncExternalStore(subscribe, snapshot);

// The source a row, a query or a mutation is about, as a stable key.
export function sourceKey(s: ListSource | null): readonly unknown[] {
  if (!s) return ["none"];
  if (s.kind === "folder") return ["folder", s.account, s.folder];
  if (s.kind === "search") return ["search", s.q, s.account, s.thread ?? ""];
  if (s.kind === "digest") return ["digest", s.list, s.list === "topic" ? s.label : ""];
  return ["all"];
}

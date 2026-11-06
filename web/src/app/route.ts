// Where you are lives in the URL, so a deep link, Back and a reload all come back to it. What the list shows is one
// type, not a set of flags: a folder (a real one, or a virtual one named __like_this__), all inboxes, or a search.
import { useSyncExternalStore } from "react";
import { navigate } from "../lib/backTrap";

export type ListSource =
  | { kind: "folder"; account: string; folder: string }
  | { kind: "allInboxes" }
  | { kind: "search"; q: string; account: string; lane?: Lane }
  | ({ kind: "digest" } & DigestList);

// Lists the digest answers rather than a folder holds: what needs you, what is safe to file, a topic from the brief.
export type DigestList = { list: "needs" | "tidy" } | { list: "topic"; label: string };

// Two built-in views read better as something other than rows. The query is the same either way.
export type Lane = "feed" | "papertrail";

export type Opened = { kind: "message"; pk: number } | { kind: "thread"; id: string; pk: number; account: string } | null;

// `brief` is the phone's way to say the brief is showing: there a pane is either the list or what's open.
export interface Route { source: ListSource | null; opened: Opened; brief?: boolean }

const BASE = "/next";
const seg = encodeURIComponent;

export function pathOf({ source, opened, brief }: Route): string {
  let path = BASE;
  const query = new URLSearchParams();
  if (source?.kind === "folder") path += `/m/${seg(source.account)}/${seg(source.folder)}`;
  else if (source?.kind === "allInboxes") path += "/all";
  else if (source?.kind === "search") {
    path += "/search";
    query.set("q", source.q);
    if (source.account) query.set("account", source.account);
    if (source.lane) query.set("lane", source.lane);
  }
  else if (source?.kind === "digest") path += source.list === "topic" ? `/brief/topic/${seg(source.label)}` : `/brief/${source.list}`;
  if (brief) query.set("brief", "1");
  if (opened?.kind === "message") query.set("m", String(opened.pk));
  else if (opened?.kind === "thread") {
    query.set("c", opened.id);
    query.set("m", String(opened.pk));
    query.set("ca", opened.account);
  }
  const q = query.toString();
  return q ? `${path}?${q}` : path;
}

export function parseRoute(pathname: string, search: string): Route {
  const q = new URLSearchParams(search);
  const parts = pathname.replace(/^\/next\/?/, "").split("/").filter(Boolean).map(decodeURIComponent);
  let source: ListSource | null = null;
  if (parts[0] === "m" && parts[1] && parts[2]) source = { kind: "folder", account: parts[1], folder: parts[2] };
  else if (parts[0] === "all") source = { kind: "allInboxes" };
  else if (parts[0] === "search") {
    const lane = q.get("lane");
    source = { kind: "search", q: q.get("q") ?? "", account: q.get("account") ?? "", ...(lane === "feed" || lane === "papertrail" ? { lane } : {}) };
  }
  else if (parts[0] === "brief" && (parts[1] === "needs" || parts[1] === "tidy")) source = { kind: "digest", list: parts[1] };
  else if (parts[0] === "brief" && parts[1] === "topic" && parts[2]) source = { kind: "digest", list: "topic", label: parts[2] };
  const pk = Number(q.get("m"));
  let opened: Opened = null;
  if (Number.isInteger(pk) && pk > 0) {
    const thread = q.get("c");
    opened = thread ? { kind: "thread", id: thread, pk, account: q.get("ca") ?? "" } : { kind: "message", pk };
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
  if (s.kind === "search") return ["search", s.q, s.account];
  if (s.kind === "digest") return ["digest", s.list, s.list === "topic" ? s.label : ""];
  return ["all"];
}

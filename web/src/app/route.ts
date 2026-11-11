// Where you are lives in the URL, so a deep link, Back, a reload and a link opened in a new tab all come back to it. What the
// list shows is one type, not a set of flags: a folder (a real one, or a virtual one named __like_this__), all inboxes, a
// virtual one over every account, a search, or the Document Hub, which holds files and not rows.
//
// The browser keeps every URL a tab goes to in its history, in plaintext, and may sync it, so no URL names anything of a
// person's. A route that holds an account, a folder, a search, a topic or an opened message is a place, `/p/<id>`: its id is
// random and made here, so going somewhere waits for nothing, and the server keeps what it stands for in the encrypted database
// and puts it back into a page opened there (`<meta name="mailmind-place">`), or loaded again for one Back comes to that the
// page doesn't know. Only a route that holds none of that is written as itself. Nothing goes into `history.state` either,
// which the browser keeps on disk too.
import { useSyncExternalStore } from "react";
import { post, refusalOf } from "../lib/api/client";
import { LIMITS } from "../lib/api/limits.gen";
import { navigate } from "../lib/backTrap";
import type { Copy } from "../lib/copy";

export const VIRTUAL_FOLDERS = ["__snoozed__", "__muted__", "__waiting__", "__replylater__"] as const;
export type VirtualFolder = (typeof VIRTUAL_FOLDERS)[number];
export const isVirtual = (name: string): name is VirtualFolder => (VIRTUAL_FOLDERS as readonly string[]).includes(name);

export type ListSource =
  | { kind: "folder"; account: string; folder: string }
  | { kind: "allInboxes" }
  | { kind: "across"; folder: VirtualFolder }
  | { kind: "search"; q: string; account: string; lane?: Lane; thread?: string; asked?: string }     // thread: only this conversation of the account. asked: the sentence `q` was translated from
  | { kind: "documents" }
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

const HOME: Route = { source: null, opened: null };
const PLACE = /^\/p\/([^/]+)$/;

// The routes written as themselves, since nothing in them is anyone's.
function readable({ source, opened, brief }: Route): string | null {
  if (opened) return null;
  const path = !source ? "/"
    : source.kind === "allInboxes" ? "/all"
    : source.kind === "across" ? `/all/${source.folder}`
    : source.kind === "documents" ? "/documents"
    : source.kind === "digest" && source.list !== "topic" ? `/brief/${source.list}`
    : null;
  return path && brief ? `${path}?brief=1` : path;
}

// A route as plain data in one order, so the same route is the same text however it was made: what a place holds.
function plain({ source, opened, brief }: Route): Route {
  return {
    source: !source ? null
      : source.kind === "folder" ? { kind: "folder", account: source.account, folder: source.folder }
      : source.kind === "search" ? { kind: "search", q: source.q, account: source.account, ...(source.lane ? { lane: source.lane } : {}), ...(source.thread ? { thread: source.thread } : {}), ...(source.asked ? { asked: source.asked } : {}) }
      : source.kind === "across" ? { kind: "across", folder: source.folder }
      : source.kind === "digest" ? (source.list === "topic" ? { kind: "digest", list: "topic", label: source.label } : { kind: "digest", list: source.list })
      : source.kind === "allInboxes" ? { kind: "allInboxes" } : { kind: "documents" },
    opened: !opened ? null
      : opened.kind === "thread" ? { kind: "thread", id: opened.id, account: opened.account, key: opened.key, folder: opened.folder, uid: opened.uid }
      : { kind: "message", key: opened.key, folder: opened.folder, uid: opened.uid },
    ...(brief ? { brief: true } : {}),
  };
}

type Fields = Record<string, unknown>;
const isObject = (v: unknown): v is Fields => typeof v === "object" && v !== null && !Array.isArray(v);
const isText = (v: unknown): v is string => typeof v === "string";
const isCount = (v: unknown): v is number => typeof v === "number" && Number.isSafeInteger(v) && v > 0;

function sourceFrom(s: unknown): ListSource | null | undefined {
  if (s === null) return null;
  if (!isObject(s)) return undefined;
  const { kind, account, folder, list, label, q, lane, thread, asked } = s;
  if (kind === "folder") return isText(account) && isText(folder) ? { kind, account, folder } : undefined;
  if (kind === "allInboxes" || kind === "documents") return { kind };
  if (kind === "across") return isText(folder) && isVirtual(folder) ? { kind, folder } : undefined;
  if (kind === "digest") {
    if (list === "needs" || list === "tidy") return { kind, list };
    return list === "topic" && isText(label) ? { kind, list, label } : undefined;
  }
  if (kind !== "search" || !isText(q) || !isText(account)) return undefined;
  return { kind, q, account, ...(lane === "feed" || lane === "papertrail" ? { lane } : {}), ...(isText(thread) && thread ? { thread } : {}), ...(isText(asked) && asked ? { asked } : {}) };
}

function openedFrom(o: unknown): Opened | undefined {
  if (o === null) return null;
  if (!isObject(o)) return undefined;
  const { kind, id, account, key, folder, uid } = o;
  if (!isCount(key) || !isCount(uid) || !isText(folder) || !folder) return undefined;
  if (kind === "message") return { kind, key, folder, uid };
  return kind === "thread" && isText(id) && id && isText(account) ? { kind, id, account, key, folder, uid } : undefined;
}

// What a place's route says, held to a route's shape: what the page reads back, it doesn't take on trust.
export function routeFrom(said: unknown): Route | null {
  if (!isObject(said) || !("source" in said) || !("opened" in said)) return null;
  const source = sourceFrom(said["source"] ?? null), opened = openedFrom(said["opened"] ?? null);
  if (source === undefined || opened === undefined) return null;
  return said["brief"] === true ? { source, opened, brief: true } : { source, opened };
}

// Each place this page knows, by its id, and its id by the route's text, so a route is one URL for as long as the page lasts.
const routes = new Map<string, Route>();
const places = new Map<string, string>();

function know(id: string, route: Route, text: string) {
  routes.set(id, route);
  places.set(text, id);
}

function newPlace(): string {
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  return btoa(String.fromCharCode(...bytes)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

// A new place goes to the server once this turn of the page is over, with the others made in it, so a link drawn with one has
// reached it long before anyone could open that link in a new tab. It goes as keepalive, so a batch on its way when the page
// goes (a reload, Back to a place made before it) still arrives, as far as the browser carries that much that way. Places the
// server couldn't be reached for go again a little later. One it refuses, or one too long to keep, lasts only as long as the
// page: a reload of it opens at home.
const unsent: { id: string; route: Fields }[] = [];
let sending = false, again: ReturnType<typeof setTimeout> | undefined;
const KEEPALIVE_BYTES = 16_384;     // the browser carries 64 KB at most in all, for every keepalive request on its way

function send() {
  sending = false;
  while (unsent.length) {
    const batch = unsent.splice(0, LIMITS.places_at_once);
    const keepalive = new TextEncoder().encode(JSON.stringify(batch)).length <= KEEPALIVE_BYTES;
    post("/api/places", { places: batch }, { keepalive }).catch((e: unknown) => {
      if (refusalOf(e)) return;
      unsent.push(...batch);
      clearTimeout(again);
      again = setTimeout(sendSoon, 5000);
    });
  }
}

function sendSoon() {
  if (sending) return;
  sending = true;
  void Promise.resolve().then(send);
}

export function pathOf(route: Route): string {
  const itself = readable(route);
  if (itself) return itself;
  const held = plain(route), text = JSON.stringify(held);
  let id = places.get(text);
  if (!id) {
    id = newPlace();
    know(id, held, text);
    if (new TextEncoder().encode(text).length <= LIMITS.place_bytes) {
      unsent.push({ id, route: { ...held } });
      sendSoon();
    }
  }
  return `/p/${id}`;
}

// The place this page was opened at, as the server put it in.
function opening() {
  if (typeof location === "undefined" || typeof document === "undefined") return;     // a test with no page
  const id = PLACE.exec(location.pathname)?.[1];
  const said = id ? document.querySelector<HTMLMetaElement>('meta[name="mailmind-place"]')?.content : "";
  if (!id || !said) return;
  try {
    const route = routeFrom(JSON.parse(said));
    if (route) know(id, route, JSON.stringify(plain(route)));
  } catch { /* no route: the page opens at home */ }
}
opening();

// Back or Forward in a page loaded again (a reload, a restored tab) can come to an entry made before it, at a place this page
// never knew. The server does, so the page is loaded from it, and nothing else hears of the move: home would take its entry.
function cameBack(e: Event) {
  const id = PLACE.exec(location.pathname)?.[1];
  if (id === undefined || routes.has(id)) return;
  e.stopImmediatePropagation();
  location.reload();
}
if (typeof window !== "undefined" && typeof window.addEventListener === "function") window.addEventListener("popstate", cameBack);     // ahead of every other listener

// A place reads as the route it holds, and one this page doesn't know as home. The readable forms of the others still read, as
// a person may type one: `/m/<account>/<folder>`, `/search?q=`, `/brief/topic/<label>`, and `?key=&folder=&uid=` for what is open.
export function parseRoute(pathname: string, search: string): Route {
  const place = PLACE.exec(pathname)?.[1];
  if (place !== undefined) return routes.get(place) ?? HOME;
  const q = new URLSearchParams(search);
  const parts = pathname.split("/").filter(Boolean).map(decodeURIComponent);
  let source: ListSource | null = null;
  if (parts[0] === "m" && parts[1] && parts[2]) source = { kind: "folder", account: parts[1], folder: parts[2] };
  else if (parts[0] === "all") source = parts[1] && isVirtual(parts[1]) ? { kind: "across", folder: parts[1] } : { kind: "allInboxes" };
  else if (parts[0] === "documents") source = { kind: "documents" };
  else if (parts[0] === "search") {
    const lane = q.get("lane");
    const thread = q.get("thread"), asked = q.get("asked");
    source = { kind: "search", q: q.get("q") ?? "", account: q.get("account") ?? "", ...(lane === "feed" || lane === "papertrail" ? { lane } : {}), ...(thread ? { thread } : {}), ...(asked ? { asked } : {}) };
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

// Where you are now, for code outside React: what the URL names, read once for each URL.
export function currentRoute(): Route {
  const now = read();
  if (now !== current) {
    current = now;
    cached = parseRoute(location.pathname, location.search);
  }
  return cached;
}

export const useRoute = () => useSyncExternalStore(subscribe, currentRoute);

// The source a row, a query or a mutation is about, as a stable key.
export function sourceKey(s: ListSource | null): readonly unknown[] {
  if (!s) return ["none"];
  if (s.kind === "folder") return ["folder", s.account, s.folder];
  if (s.kind === "across") return ["across", s.folder];
  if (s.kind === "documents") return ["documents"];
  if (s.kind === "search") return ["search", s.q, s.account, s.thread ?? ""];
  if (s.kind === "digest") return ["digest", s.list, s.list === "topic" ? s.label : ""];
  return ["all"];
}

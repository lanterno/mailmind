import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { LIMITS } from "../lib/api/limits.gen";
import { parseRoute, pathOf, routeFrom, sourceKey, type Route } from "./route";

// Routes that hold something of a person's: an account, a folder, a search, a question, a topic, or what is open.
const personal: Route[] = [
  { source: { kind: "folder", account: "ada@example.test", folder: "INBOX" }, opened: null },
  { source: { kind: "folder", account: "ada@example.test", folder: "Lists/Yarn & Wool" }, opened: { kind: "message", key: 12, folder: "Lists/Yarn & Wool", uid: 40 } },
  { source: { kind: "folder", account: "ada@example.test", folder: "__waiting__" }, opened: { kind: "thread", id: "rf:<a b@example.test>", account: "ada@example.test", key: 3, folder: "Sent Items", uid: 7 } },
  { source: { kind: "allInboxes" }, opened: { kind: "message", key: 9, folder: "INBOX", uid: 1 } },
  { source: { kind: "across", folder: "__muted__" }, opened: { kind: "thread", id: "rf:<a@example.test>", account: "bea@example.test", key: 4, folder: "INBOX", uid: 2 } },
  { source: { kind: "search", q: "from:lin is:unread & more", account: "ada@example.test" }, opened: null },
  { source: { kind: "search", q: "noodle", account: "ada@example.test", thread: "rf:ada@example.test:49f7" }, opened: null },
  { source: { kind: "search", q: "from:lin subject:lunch", account: "", asked: "emails from lin about lunch" }, opened: null },
  { source: { kind: "search", q: "kind:x", account: "", lane: "papertrail" }, opened: null },
  { source: { kind: "digest", list: "topic", label: "Trips & travel" }, opened: null },
  { source: { kind: "documents" }, opened: { kind: "message", key: 7, folder: "INBOX", uid: 70 } },
  { source: { kind: "folder", account: "ada@example.test", folder: "INBOX" }, opened: null, brief: true },
];
// And those written as themselves, since nothing in them is anyone's.
const readable: [Route, string][] = [
  [{ source: null, opened: null }, "/"],
  [{ source: null, opened: null, brief: true }, "/?brief=1"],
  [{ source: { kind: "allInboxes" }, opened: null }, "/all"],
  [{ source: { kind: "across", folder: "__snoozed__" }, opened: null }, "/all/__snoozed__"],
  [{ source: { kind: "across", folder: "__replylater__" }, opened: null, brief: true }, "/all/__replylater__?brief=1"],
  [{ source: { kind: "documents" }, opened: null }, "/documents"],
  [{ source: { kind: "digest", list: "needs" }, opened: null }, "/brief/needs"],
  [{ source: { kind: "digest", list: "tidy" }, opened: null }, "/brief/tidy"],
];
const named = (r: Route) => JSON.stringify(r);
// What of a route is a person's: no URL may hold any of it.
function words({ source, opened }: Route): string[] {
  const said = [source && "account" in source ? source.account : "", source && "folder" in source ? source.folder : "",
                source?.kind === "search" ? [source.q, source.thread ?? "", source.asked ?? ""] : [],
                source?.kind === "digest" && source.list === "topic" ? source.label : "",
                opened?.folder ?? "", opened?.kind === "thread" ? [opened.id, opened.account] : []];
  return said.flat().flatMap((w) => [w, encodeURIComponent(w), w.split("@")[0]!]).filter((w) => w.length > 2);
}

let posted: { url: string; token: string | null; body: { places: { id: string; route: Route }[] } }[];
let answer: () => Promise<Response>;

beforeEach(() => {
  posted = [];
  answer = () => Promise.resolve(new Response(JSON.stringify({ ok: true }), { headers: { "Content-Type": "application/json" } }));
  vi.stubGlobal("document", { querySelector: (q: string) => (q === 'meta[name="csrf"]' ? { content: "the-token" } : null) });
  vi.stubGlobal("fetch", vi.fn((url: string, init: RequestInit) => {
    posted.push({ url, token: new Headers(init.headers).get("X-CSRF-Token"), body: JSON.parse(init.body as string) as never });
    return answer();
  }));
});
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});
const turn = () => new Promise((done) => setTimeout(done, 0));     // what this turn made has gone

describe("the URL", () => {
  it.each(readable.map(([r, path]) => [path, r] as const))("writes %s as itself", (path, route) => {
    expect(pathOf(route)).toBe(path);
    const u = new URL(path, "http://127.0.0.1");
    expect(parseRoute(u.pathname, u.search)).toEqual(route);
  });
  it.each(personal.map((r) => [named(r), r] as const))("writes %s as a place, which reads back as it", (_, route) => {
    const path = pathOf(route);
    expect(path).toMatch(/^\/p\/[A-Za-z0-9_-]{22}$/);
    expect(parseRoute(path, "")).toEqual(route);
    for (const word of words(route)) {
      expect(path).not.toContain(word);
      expect(decodeURIComponent(path)).not.toContain(word);
    }
  });
  it("gives a route one URL for the page's life, however the route was made, and another route another", () => {
    const route: Route = { source: { kind: "search", q: "quilt", account: "ada@example.test" }, opened: null };
    const path = pathOf(route);
    expect(pathOf({ opened: null, source: { account: "ada@example.test", q: "quilt", kind: "search" } })).toBe(path);
    expect(pathOf({ source: { kind: "search", q: "quilt", account: "ada@example.test", thread: "", asked: "" }, opened: null, brief: false })).toBe(path);
    expect(pathOf({ source: { kind: "search", q: "quilts", account: "ada@example.test" }, opened: null })).not.toBe(path);
    expect(pathOf({ ...route, brief: true })).not.toBe(path);
    expect(new Set(personal.map(pathOf)).size).toBe(personal.length);
  });
  it("reads a place it doesn't know as home", () => {
    expect(parseRoute("/p/AAAAAAAAAAAAAAAAAAAAAA", "")).toEqual({ source: null, opened: null });
    expect(parseRoute("/p/nothing-here", "?key=9&folder=INBOX&uid=4")).toEqual({ source: null, opened: null });
  });
  it("still reads the forms a person may type", () => {
    expect(parseRoute("/m/ada%40example.test/Lists%2FYarn%20%26%20Wool", "?key=12&folder=Lists%2FYarn+%26+Wool&uid=40")).toEqual(personal[1]);
    expect(parseRoute("/search", "?q=from%3Alin+subject%3Alunch&asked=emails+from+lin+about+lunch")).toEqual(personal[7]);
    expect(parseRoute("/brief/topic/Trips%20%26%20travel", "")).toEqual(personal[9]);
    expect(parseRoute("/all/__muted__", "?c=rf%3A%3Ca%40example.test%3E&ca=bea%40example.test&key=4&folder=INBOX&uid=2")).toEqual(personal[4]);
    expect(parseRoute("/m/ada%40example.test/INBOX", "?brief=1")).toEqual(personal[11]);
  });
  it("ignores an open that isn't a copy", () => {
    expect(parseRoute("/all", "?key=abc&folder=INBOX&uid=1").opened).toBeNull();
    expect(parseRoute("/all", "?key=-2&folder=INBOX&uid=1").opened).toBeNull();
    expect(parseRoute("/all", "?key=1.5&folder=INBOX&uid=1").opened).toBeNull();
    expect(parseRoute("/all", "?key=9&uid=1").opened).toBeNull();
    expect(parseRoute("/all", "?key=9&folder=&uid=1").opened).toBeNull();
  });
  it("opens nothing without the copy's uid, or with one that isn't a whole number", () => {
    expect(parseRoute("/all", "?key=9&folder=INBOX").opened).toBeNull();
    for (const uid of ["", "0", "-1", "1.5", "x", " 1", "01", "99999999999999999999"])
      expect(parseRoute("/all", `?key=9&folder=INBOX&uid=${encodeURIComponent(uid)}`).opened).toBeNull();
    expect(parseRoute("/all", "?key=9&folder=INBOX&uid=4").opened).toEqual({ kind: "message", key: 9, folder: "INBOX", uid: 4 });
  });
  it("opens nothing from a link that named a row by its number", () => {
    expect(parseRoute("/all", "?m=12").opened).toBeNull();
    expect(parseRoute("/m/a%40example.test/INBOX", "?c=rf%3Ax&m=12&ca=a%40example.test").opened).toBeNull();
  });
  it("takes only the views that aren't folders under /all", () => {
    expect(parseRoute("/all/__waiting__", "").source).toEqual({ kind: "across", folder: "__waiting__" });
    expect(parseRoute("/all/__screener__", "").source).toEqual({ kind: "allInboxes" });
    expect(parseRoute("/all/INBOX", "").source).toEqual({ kind: "allInboxes" });
  });
  it("takes a path it doesn't know as nowhere", () => {
    expect(parseRoute("/m/only-account", "").source).toBeNull();
    expect(parseRoute("/nope", "").source).toBeNull();
  });
});

describe("a place, to the server", () => {
  const folder = (n: number): Route => ({ source: { kind: "folder", account: "ada@example.test", folder: `Projects/${n}-${Math.random()}` }, opened: null });

  it("goes once this turn is over, with the others the turn made, with the page's token", async () => {
    const a = folder(1), b = folder(2);
    const ids = [pathOf(a), pathOf(b), pathOf(a)].map((p) => p.slice(3));
    expect(posted).toEqual([]);                    // navigating waits for nothing
    await turn();
    expect(posted).toEqual([{ url: "/api/places", token: "the-token", body: { places: [{ id: ids[0], route: a }, { id: ids[1], route: b }] } }]);
    expect(ids[2]).toBe(ids[0]);
  });
  it("goes once: a route the page has already sent is the place it was", async () => {
    const a = folder(1);
    pathOf(a);
    await turn();
    pathOf(a);
    pathOf({ ...a });
    await turn();
    expect(posted).toHaveLength(1);
  });
  it("goes as more requests past what one may name", async () => {
    const made = Array.from({ length: LIMITS.places_at_once * 2 + 1 }, (_, n) => pathOf(folder(n)).slice(3));
    await turn();
    expect(posted.map((p) => p.body.places.length)).toEqual([LIMITS.places_at_once, LIMITS.places_at_once, 1]);
    expect(posted.flatMap((p) => p.body.places.map((x) => x.id))).toEqual(made);
  });
  it("goes again a little later when the server can't be reached, and not when it refuses", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    answer = () => Promise.reject(new TypeError("Failed to fetch"));
    const route = folder(1), path = pathOf(route);
    await vi.advanceTimersByTimeAsync(0);
    expect(posted).toHaveLength(1);
    answer = () => Promise.resolve(new Response(JSON.stringify({ type: "/problems/refused", status: 400 }), { status: 400 }));
    await vi.advanceTimersByTimeAsync(5000);
    expect(posted.map((p) => p.body.places[0]!.id)).toEqual([path.slice(3), path.slice(3)]);
    await vi.advanceTimersByTimeAsync(60_000);
    expect(posted).toHaveLength(2);
    expect(parseRoute(path, "")).toEqual(route);                // and lasts as long as the page
  });
  it("doesn't go when it is too long to keep, and lasts as long as the page", async () => {
    const long: Route = { source: { kind: "search", q: "x".repeat(LIMITS.place_bytes), account: "" }, opened: null };
    const path = pathOf(long);
    await turn();
    expect(posted).toEqual([]);
    expect(path).toMatch(/^\/p\//);
    expect(parseRoute(path, "")).toEqual(long);
  });
});

describe("the place a page was opened at", () => {
  const ID = "Opened-At-This-Place00";
  async function opened(content: string | null, pathname = `/p/${ID}`) {
    vi.resetModules();
    vi.stubGlobal("location", { pathname, search: "" });
    vi.stubGlobal("document", { querySelector: (q: string) => (q === 'meta[name="mailmind-place"]' && content !== null ? { content } : q === 'meta[name="csrf"]' ? { content: "the-token" } : null) });
    return import("./route");
  }

  it("reads as the route the server put in, and is the URL that route is for as long as the page lasts", async () => {
    const route = personal[2]!;
    const page = await opened(JSON.stringify(route));
    expect(page.parseRoute(`/p/${ID}`, "")).toEqual(route);
    expect(page.currentRoute()).toEqual(route);
    expect(page.pathOf(structuredClone(route))).toBe(`/p/${ID}`);
    await turn();
    expect(posted).toEqual([]);                    // the server has it
  });
  it.each([["no meta", null], ["an empty one", ""], ["one that isn't JSON", "{oops"], ["one that isn't a route", '{"source":{"kind":"folder","account":7}}'],
           ["a list", "[]"]])("with %s is home", async (_, content) => {
    const page = await opened(content);
    expect(page.parseRoute(`/p/${ID}`, "")).toEqual({ source: null, opened: null });
  });
  it("is only the place the page was opened at", async () => {
    const page = await opened(JSON.stringify(personal[0]), "/all");
    expect(page.parseRoute(`/p/${ID}`, "")).toEqual({ source: null, opened: null });
  });
});

describe("a route read back", () => {
  it.each([...personal, ...readable.map(([r]) => r)].map((r) => [named(r), r] as const))("%s reads as it was", (_, route) => {
    expect(routeFrom(JSON.parse(JSON.stringify(route)))).toEqual(route);
  });
  it.each([
    null, [], "a route", 7, {},
    { source: "INBOX", opened: null },
    { source: { kind: "elsewhere" }, opened: null },
    { source: { kind: "folder", account: "ada@example.test" }, opened: null },
    { source: { kind: "folder", account: ["ada@example.test"], folder: "INBOX" }, opened: null },
    { source: { kind: "across", folder: "INBOX" }, opened: null },
    { source: { kind: "search", account: "" }, opened: null },
    { source: { kind: "digest", list: "everything" }, opened: null },
    { source: { kind: "digest", list: "topic", label: 3 }, opened: null },
    { source: null, opened: { kind: "message", key: 0, folder: "INBOX", uid: 1 } },
    { source: null, opened: { kind: "message", key: 9, folder: "INBOX", uid: 1.5 } },
    { source: null, opened: { kind: "message", key: "9", folder: "INBOX", uid: 1 } },
    { source: null, opened: { kind: "message", key: 9, folder: "", uid: 1 } },
    { source: null, opened: { kind: "message", key: 2 ** 53, folder: "INBOX", uid: 1 } },
    { source: null, opened: { kind: "thread", key: 9, folder: "INBOX", uid: 1, account: "" } },
    { source: null, opened: { kind: "copy", key: 9, folder: "INBOX", uid: 1 } },
  ].map((said) => [JSON.stringify(said), said] as const))("%s is no route", (_, said) => {
    expect(routeFrom(said)).toBeNull();
  });
  it("leaves out what it doesn't know, as the URL did", () => {
    expect(routeFrom({ source: { kind: "search", q: "x", account: "", lane: "nonsense", thread: 7, asked: "" }, opened: null, brief: "yes", more: 1 }))
      .toEqual({ source: { kind: "search", q: "x", account: "" }, opened: null });
  });
});

describe("the lanes", () => {
  it.each(["feed", "papertrail"] as const)("%s keeps its lane through the URL", (lane) => {
    const route = { source: { kind: "search", q: "kind:x", account: "", lane }, opened: null } as const;
    expect(parseRoute(pathOf(route), "")).toEqual(route);
    expect(parseRoute("/search", `?q=kind%3Ax&lane=${lane}`)).toEqual(route);
  });

  it("takes a lane it doesn't know as a plain search", () => {
    expect(parseRoute("/search", "?q=x&lane=nonsense").source).toEqual({ kind: "search", q: "x", account: "" });
  });
});

describe("lists from the digest, and the brief", () => {
  it.each([
    [{ kind: "digest", list: "needs" }, "/brief/needs"],
    [{ kind: "digest", list: "tidy" }, "/brief/tidy"],
    [{ kind: "digest", list: "topic", label: "Trips & travel" }, "/brief/topic/Trips%20%26%20travel"],
  ] as const)("%o reads from its path", (source, path) => {
    expect(parseRoute(path, "")).toEqual({ source, opened: null });
    expect(parseRoute(pathOf({ source, opened: null }), "")).toEqual({ source, opened: null });
  });
  it("says the brief is showing only when asked to", () => {
    const source = { kind: "folder", account: "a@example.test", folder: "INBOX" } as const;
    expect(parseRoute(pathOf({ source, opened: null, brief: true }), "")).toEqual({ source, opened: null, brief: true });
    expect(parseRoute(pathOf({ source, opened: null }), "")).toEqual({ source, opened: null });
    expect(parseRoute("/m/a%40example.test/INBOX", "?brief=1")).toEqual({ source, opened: null, brief: true });
    expect(parseRoute("/m/a%40example.test/INBOX", "")).toEqual({ source, opened: null });
  });
  it("gives each list its own key", () => {
    expect(sourceKey({ kind: "digest", list: "needs" })).not.toEqual(sourceKey({ kind: "digest", list: "tidy" }));
    expect(sourceKey({ kind: "digest", list: "topic", label: "A" })).not.toEqual(sourceKey({ kind: "digest", list: "topic", label: "B" }));
    expect(sourceKey({ kind: "search", q: "x", account: "a", thread: "t" })).not.toEqual(sourceKey({ kind: "search", q: "x", account: "a" }));
  });
});

describe("the Document Hub", () => {
  it("is a place of its own, and a file opened from it stays there", () => {
    expect(pathOf({ source: { kind: "documents" }, opened: null })).toBe("/documents");
    expect(parseRoute("/documents", "")).toEqual({ source: { kind: "documents" }, opened: null });
    const opened = { kind: "message", key: 7, folder: "INBOX", uid: 70 } as const;
    expect(parseRoute("/documents", "?key=7&folder=INBOX&uid=70")).toEqual({ source: { kind: "documents" }, opened });
    expect(parseRoute(pathOf({ source: { kind: "documents" }, opened }), "")).toEqual({ source: { kind: "documents" }, opened });
    expect(sourceKey({ kind: "documents" })).toEqual(["documents"]);
  });
});

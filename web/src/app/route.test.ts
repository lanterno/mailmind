import { describe, expect, it } from "vitest";
import { parseRoute, pathOf, sourceKey, type Route } from "./route";

const routes: Route[] = [
  { source: null, opened: null },
  { source: { kind: "folder", account: "ada@example.test", folder: "INBOX" }, opened: null },
  { source: { kind: "folder", account: "ada@example.test", folder: "Lists/Yarn & Wool" }, opened: { kind: "message", key: 12, folder: "Lists/Yarn & Wool", uid: 40 } },
  { source: { kind: "folder", account: "ada@example.test", folder: "__waiting__" }, opened: { kind: "thread", id: "rf:<a b@example.test>", account: "ada@example.test", key: 3, folder: "Sent Items", uid: 7 } },
  { source: { kind: "allInboxes" }, opened: { kind: "message", key: 9, folder: "INBOX", uid: 1 } },
  { source: { kind: "search", q: "from:lin is:unread & more", account: "ada@example.test" }, opened: null },
];

describe("the URL", () => {
  it.each(routes.map((r) => [pathOf(r), r] as const))("round-trips %s", (path, route) => {
    const u = new URL(path, "http://127.0.0.1");
    expect(parseRoute(u.pathname, u.search)).toEqual(route);
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
  it("takes a path it doesn't know as nowhere", () => {
    expect(parseRoute("/m/only-account", "").source).toBeNull();
    expect(parseRoute("/nope", "").source).toBeNull();
  });
});

describe("the lanes", () => {
  it.each(["feed", "papertrail"] as const)("%s keeps its lane through the URL", (lane) => {
    const route = { source: { kind: "search", q: "kind:x", account: "", lane }, opened: null } as const;
    const u = new URL(pathOf(route), "http://127.0.0.1");
    expect(u.searchParams.get("lane")).toBe(lane);
    expect(parseRoute(u.pathname, u.search)).toEqual(route);
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
  ] as const)("%o has a path of its own", (source, path) => {
    expect(pathOf({ source, opened: null })).toBe(path);
    expect(parseRoute(path, "")).toEqual({ source, opened: null });
  });
  it("says the brief is showing only when asked to", () => {
    const source = { kind: "folder", account: "a@example.test", folder: "INBOX" } as const;
    const path = pathOf({ source, opened: null, brief: true });
    expect(path).toContain("?brief=1");
    expect(parseRoute(path.split("?")[0]!, "?brief=1")).toEqual({ source, opened: null, brief: true });
    expect(parseRoute(path.split("?")[0]!, "")).toEqual({ source, opened: null });
  });
  it("gives each list its own key", () => {
    expect(sourceKey({ kind: "digest", list: "needs" })).not.toEqual(sourceKey({ kind: "digest", list: "tidy" }));
    expect(sourceKey({ kind: "digest", list: "topic", label: "A" })).not.toEqual(sourceKey({ kind: "digest", list: "topic", label: "B" }));
  });
});

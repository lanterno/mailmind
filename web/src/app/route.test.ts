import { describe, expect, it } from "vitest";
import { parseRoute, pathOf, sourceKey, type Route } from "./route";

const routes: Route[] = [
  { source: null, opened: null },
  { source: { kind: "folder", account: "ada@example.test", folder: "INBOX" }, opened: null },
  { source: { kind: "folder", account: "ada@example.test", folder: "Lists/Yarn & Wool" }, opened: { kind: "message", pk: 12 } },
  { source: { kind: "folder", account: "ada@example.test", folder: "__waiting__" }, opened: { kind: "thread", id: "rf:<a b@example.test>", pk: 3, account: "ada@example.test" } },
  { source: { kind: "allInboxes" }, opened: { kind: "message", pk: 9 } },
  { source: { kind: "search", q: "from:lin is:unread & more", account: "ada@example.test" }, opened: null },
];

describe("the URL", () => {
  it.each(routes.map((r) => [pathOf(r), r] as const))("round-trips %s", (path, route) => {
    const u = new URL(path, "http://127.0.0.1");
    expect(parseRoute(u.pathname, u.search)).toEqual(route);
  });
  it("ignores an open that isn't a message", () => {
    expect(parseRoute("/next/all", "?m=abc").opened).toBeNull();
    expect(parseRoute("/next/all", "?m=-2").opened).toBeNull();
  });
  it("takes a path it doesn't know as nowhere", () => {
    expect(parseRoute("/next/m/only-account", "").source).toBeNull();
    expect(parseRoute("/next/nope", "").source).toBeNull();
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
    expect(parseRoute("/next/search", "?q=x&lane=nonsense").source).toEqual({ kind: "search", q: "x", account: "" });
  });
});

describe("lists from the digest, and the brief", () => {
  it.each([
    [{ kind: "digest", list: "needs" }, "/next/brief/needs"],
    [{ kind: "digest", list: "tidy" }, "/next/brief/tidy"],
    [{ kind: "digest", list: "topic", label: "Trips & travel" }, "/next/brief/topic/Trips%20%26%20travel"],
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

import { describe, expect, it } from "vitest";
import { parseRoute, pathOf, type Route } from "./route";

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

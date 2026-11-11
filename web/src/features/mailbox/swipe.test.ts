import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Copy } from "../../lib/copy";
import { cleanup, renderHook } from "../../lib/testing/renderHook";
import { useSwipe } from "./swipe";

type Handler = (e: object) => void;

// A phone's list: the rows, the badge that says what a swipe will do, and a finger on the glass.
function aList() {
  const on = new Map<string, Handler>();
  const list = {
    addEventListener: (type: string, fn: Handler) => void on.set(type, fn),
    removeEventListener: (type: string, fn: Handler) => void (on.get(type) === fn && on.delete(type)),
  };
  const badge = { className: "", style: {} as Record<string, string> };
  const row = { dataset: { key: "7", folder: "INBOX", uid: "70" }, style: {} as Record<string, string>, getBoundingClientRect: () => ({ top: 100, height: 60 }) };
  const onRow = { closest: (sel: string) => (sel === ".cbx" ? null : row) };
  const onBox = { closest: () => row };
  vi.stubGlobal("document", { getElementById: (id: string) => ({ list, swipeBadge: badge })[id] ?? null });
  let blocked = 0;
  const touch = (type: string, x: number, y: number, target: object = onRow, fingers = 1) =>
    on.get(type)?.({ target, touches: Array.from({ length: fingers }, () => ({ clientX: x, clientY: y })), preventDefault: () => void blocked++ });
  const swipe = (dx: number, dy = 0, target: object = onRow) => {
    touch("touchstart", 200, 300, target);
    touch("touchmove", 200 + dx / 2, 300 + dy / 2);
    touch("touchmove", 200 + dx, 300 + dy);
    on.get("touchend")?.({});
  };
  return { on, badge, row, onBox, touch, swipe, blocked: () => blocked };
}

const archive = vi.fn<(c: Copy) => void>(), snooze = vi.fn<(c: Copy) => void>();
const swiping = () => renderHook((verbs: { archive: typeof archive; snooze: typeof snooze }) => useSwipe(verbs.archive, verbs.snooze), { props: { archive, snooze } });
const copy = { key: 7, folder: "INBOX", uid: 70 };

beforeEach(() => [archive, snooze].forEach((fn) => fn.mockReset()));
afterEach(async () => {
  await cleanup();
  vi.unstubAllGlobals();
});

describe("a row swiped", () => {
  it("left past the mark archives it", async () => {
    const page = aList();
    await swiping();
    page.swipe(-120);
    expect(archive).toHaveBeenCalledWith(copy);
    expect(snooze).not.toHaveBeenCalled();
    expect(page.row.style["transform"]).toBe("");
  });
  it("right past the mark snoozes it", async () => {
    const page = aList();
    await swiping();
    page.swipe(120);
    expect(snooze).toHaveBeenCalledWith(copy);
  });
  it("not far enough springs back and does nothing", async () => {
    const page = aList();
    await swiping();
    page.swipe(-60);
    expect(archive).not.toHaveBeenCalled();
    expect(page.row.style["transform"]).toBe("");
    expect(page.row.style["transition"]).toMatch(/^transform/);
  });
  it("follows the finger, and the badge says what letting go will do", async () => {
    const page = aList();
    await swiping();
    page.touch("touchstart", 200, 300);
    page.touch("touchmove", 120, 302);
    expect(page.row.style["transform"]).toBe("translateX(-80px)");
    expect(page.badge.className).toBe("show archive");
    expect(page.badge.style["top"]).toBe("113px");
    page.touch("touchmove", 300, 302);
    expect(page.badge.className).toBe("show snooze");
    page.touch("touchmove", 205, 302);
    expect(page.badge.className).toBe("");
    page.on.get("touchend")!({});
    expect(page.badge.className).toBe("");
  });
});

describe("what stays the list's", () => {
  it("a drag mostly up or down scrolls, and is never a swipe", async () => {
    const page = aList();
    await swiping();
    page.swipe(-100, 140);
    expect(archive).not.toHaveBeenCalled();
    expect(page.blocked()).toBe(0);
    expect(page.row.style["transform"]).toBeUndefined();
  });
  it("a wobble too small to tell which way is nothing yet", async () => {
    const page = aList();
    await swiping();
    page.touch("touchstart", 200, 300);
    page.touch("touchmove", 206, 304);
    expect(page.blocked()).toBe(0);
    page.on.get("touchend")!({});
    expect(archive).not.toHaveBeenCalled();
    expect(snooze).not.toHaveBeenCalled();
  });
  it("the select box is a select box", async () => {
    const page = aList();
    await swiping();
    page.swipe(-120, 0, page.onBox);
    expect(archive).not.toHaveBeenCalled();
  });
  it("two fingers aren't a swipe", async () => {
    const page = aList();
    await swiping();
    page.touch("touchstart", 200, 300, undefined, 2);
    page.touch("touchmove", 60, 300);
    page.on.get("touchend")!({});
    expect(archive).not.toHaveBeenCalled();
  });
});

it("does what the newest verbs say, though they changed after it began listening", async () => {
  const page = aList();
  const r = await swiping();
  const later = vi.fn<(c: Copy) => void>();
  await r.rerender({ archive: later, snooze });
  page.swipe(-120);
  expect(later).toHaveBeenCalledWith(copy);
  expect(archive).not.toHaveBeenCalled();
});

it("stops listening when the list goes", async () => {
  const page = aList();
  await swiping();
  expect([...page.on.keys()].sort()).toEqual(["touchcancel", "touchend", "touchmove", "touchstart"]);
  await cleanup();
  expect(page.on.size).toBe(0);
});

it("does nothing on a page with no list", async () => {
  vi.stubGlobal("document", { getElementById: () => null });
  await expect(swiping()).resolves.toBeDefined();
});

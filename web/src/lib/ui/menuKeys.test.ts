import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, renderHook } from "../testing/renderHook";
import { useDigitKeys, useFocusFirst } from "./menuKeys";

type Listener = (e: object) => void;

// The page's keys, and what a key pressed on it did.
function aPage() {
  const listening = new Set<Listener>();
  vi.stubGlobal("document", {
    addEventListener: (_: string, fn: Listener) => void listening.add(fn),
    removeEventListener: (_: string, fn: Listener) => void listening.delete(fn),
  });
  const press = (key: string, over: object = {}) => {
    const e = { key, metaKey: false, ctrlKey: false, altKey: false, target: { closest: () => null }, preventDefault: vi.fn(), ...over };
    listening.forEach((fn) => fn(e));
    return e;
  };
  return { press, listening };
}

afterEach(async () => {
  await cleanup();
  vi.unstubAllGlobals();
});

describe("a menu's numbered choices", () => {
  const pick = vi.fn();
  const menu = (count: number, chosen = pick) => renderHook((p: { count: number; pick: typeof pick }) => useDigitKeys(p.count, p.pick), { props: { count, pick: chosen } });
  beforeEach(() => pick.mockReset());

  it("are picked by 1 to n, and the key goes no further", async () => {
    const page = aPage();
    await menu(4);
    expect(page.press("1").preventDefault).toHaveBeenCalled();
    page.press("4");
    expect(pick.mock.calls).toEqual([[0], [3]]);
  });
  it("aren't picked by a digit past the last, by 0, or by any other key", async () => {
    const page = aPage();
    await menu(3);
    for (const key of ["4", "9", "0", "a", "Enter"]) expect(page.press(key).preventDefault).not.toHaveBeenCalled();
    expect(pick).not.toHaveBeenCalled();
  });
  it("leave a shortcut with a modifier to whatever it does", async () => {
    const page = aPage();
    await menu(3);
    page.press("1", { metaKey: true });
    page.press("2", { ctrlKey: true });
    page.press("3", { altKey: true });
    expect(pick).not.toHaveBeenCalled();
  });
  it("leave a digit typed into the menu's own time field to the time", async () => {
    const page = aPage();
    await menu(3);
    page.press("2", { target: { closest: (sel: string) => (sel === "input" ? {} : null) } });
    expect(pick).not.toHaveBeenCalled();
  });
  it("follow the menu as its choices change", async () => {
    const page = aPage(), later = vi.fn();
    const r = await menu(2);
    await r.rerender({ count: 5, pick: later });
    page.press("5");
    expect(later).toHaveBeenCalledWith(4);
    expect(pick).not.toHaveBeenCalled();
  });
  it("stop being picked once the menu has gone", async () => {
    const page = aPage();
    await menu(3);
    await cleanup();
    page.press("1");
    expect(pick).not.toHaveBeenCalled();
    expect(page.listening.size).toBe(0);
  });
});

describe("a popover's first control", () => {
  function aFrame() {
    const frames = new Map<number, () => void>();
    let next = 0;
    vi.stubGlobal("requestAnimationFrame", (fn: () => void) => (frames.set(++next, fn), next));
    vi.stubGlobal("cancelAnimationFrame", (id: number) => void frames.delete(id));
    return { paint: () => frames.forEach((fn) => fn()), pending: () => frames.size };
  }
  const popover = (focus: () => void, asked: string[] = []) => ({ querySelector: (sel: string) => (asked.push(sel), { focus }) });

  it("takes the focus once the popover is on screen", async () => {
    const frame = aFrame(), focus = vi.fn(), asked: string[] = [];
    const r = await renderHook(() => useFocusFirst());
    (r.result as { current: unknown }).current = popover(focus, asked);
    expect(focus).not.toHaveBeenCalled();
    frame.paint();
    expect(focus).toHaveBeenCalled();
    expect(asked).toEqual(["button"]);
  });
  it("can be another kind of control", async () => {
    const frame = aFrame(), asked: string[] = [];
    const r = await renderHook(() => useFocusFirst("input"));
    (r.result as { current: unknown }).current = popover(vi.fn(), asked);
    frame.paint();
    expect(asked).toEqual(["input"]);
  });
  it("takes nothing if the popover closed first", async () => {
    const frame = aFrame();
    await renderHook(() => useFocusFirst());
    await cleanup();
    expect(frame.pending()).toBe(0);
  });
});

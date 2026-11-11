import { act } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, renderHook } from "./testing/renderHook";

// A tab's history: its entries, where it is, and a traversal that lands later, as a browser's does.
function aTab() {
  const entries: unknown[] = [null];
  let at = 0, landing = 0;
  const history = {
    get state() {
      return entries[at];
    },
    pushState: vi.fn((state: unknown) => {
      entries.splice(at + 1, Infinity, state);
      at++;
    }),
    replaceState: vi.fn((state: unknown) => void (entries[at] = state)),
    back: vi.fn(() => void landing++),
  };
  vi.stubGlobal("history", history);
  vi.stubGlobal("location", { href: "http://127.0.0.1:8721/all" });
  const pop = () => act(async () => {
    at = Math.max(0, at - 1);
    window.dispatchEvent(new Event("popstate"));
  });
  return {
    history,
    length: () => at + 1,
    // A person pressing Back.
    pressBack: pop,
    // What history.back() asked for, arriving.
    land: async () => {
      while (landing) {
        landing--;
        await pop();
      }
    },
  };
}

let trap: typeof import("./backTrap");
interface Layer { open: boolean; close: () => void | false }
const layer = (open: boolean, close: () => void | false = vi.fn()) =>
  renderHook((p: Layer) => trap.useLayer(p.open, p.close), { props: { open, close } });

beforeEach(async () => {
  vi.resetModules();
  trap = await import("./backTrap");
});
afterEach(async () => {
  await cleanup();
  vi.unstubAllGlobals();
});

describe("Back with something open", () => {
  it("closes it, and spends only the entry held for it", async () => {
    const tab = aTab(), close = vi.fn();
    await layer(true, close);
    expect(tab.length()).toBe(2);
    await tab.pressBack();
    expect(close).toHaveBeenCalledTimes(1);
    expect(tab.length()).toBe(1);
    expect(tab.history.pushState).toHaveBeenCalledTimes(1);
  });
  it("holds no entry while nothing is open", async () => {
    const tab = aTab();
    await layer(false);
    expect(tab.history.pushState).not.toHaveBeenCalled();
  });
  it("closes the top of two, and the next Back the one under it", async () => {
    const tab = aTab(), under = vi.fn(), top = vi.fn();
    await layer(true, under);
    await layer(true, top);
    expect(tab.history.pushState).toHaveBeenCalledTimes(1);
    await tab.pressBack();
    expect(top).toHaveBeenCalled();
    expect(under).not.toHaveBeenCalled();
    expect(tab.length()).toBe(2);
    await tab.pressBack();
    expect(under).toHaveBeenCalled();
    expect(tab.length()).toBe(1);
  });
  it("leaves open a layer that took Back for something smaller, and holds an entry for the next Back", async () => {
    const tab = aTab();
    let inner = true;
    const close = vi.fn(() => (inner ? ((inner = false), false as const) : undefined));
    await layer(true, close);
    await tab.pressBack();
    expect(tab.length()).toBe(2);
    await tab.pressBack();
    expect(close).toHaveBeenCalledTimes(2);
    expect(tab.length()).toBe(1);
  });
  it("calls the newest close the layer was given", async () => {
    const tab = aTab(), first = vi.fn(), second = vi.fn();
    const r = await layer(true, first);
    await r.rerender({ open: true, close: second });
    await tab.pressBack();
    expect(second).toHaveBeenCalled();
    expect(first).not.toHaveBeenCalled();
  });
});

describe("a layer closed some other way", () => {
  it("gives its entry back, so the next Back isn't swallowed", async () => {
    const tab = aTab();
    const r = await layer(true);
    await r.rerender({ open: false, close: vi.fn() });
    expect(tab.history.back).toHaveBeenCalledTimes(1);
    await tab.land();
    expect(tab.length()).toBe(1);
  });
  it("pushes nothing while the entry is on its way back, and holds one again once it lands", async () => {
    const tab = aTab();
    const first = await layer(true);
    await first.rerender({ open: false, close: vi.fn() });
    const close = vi.fn();
    await layer(true, close);
    expect(tab.history.pushState).toHaveBeenCalledTimes(1);
    await tab.land();
    expect(tab.history.pushState).toHaveBeenCalledTimes(2);
    expect(tab.length()).toBe(2);
    expect(close).not.toHaveBeenCalled();
  });
});

describe("going somewhere from inside a layer", () => {
  it("takes the held entry's place, so no dead Back sits under the new page", async () => {
    aTab();
    await layer(true);
    const apply = vi.fn();
    trap.navigate(apply);
    expect(apply).toHaveBeenCalledWith(true);
  });
  it("adds an entry as usual with nothing open", async () => {
    aTab();
    const apply = vi.fn();
    trap.navigate(apply);
    expect(apply).toHaveBeenCalledWith(false);
  });
  it("waits for a traversal on its way, which would otherwise undo it", async () => {
    const tab = aTab();
    const r = await layer(true);
    await r.rerender({ open: false, close: vi.fn() });
    const apply = vi.fn();
    trap.navigate(apply);
    expect(apply).not.toHaveBeenCalled();
    await tab.land();
    expect(apply).toHaveBeenCalledWith(false);
  });
});

import { act } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { createStore, store, stored } from "./store";
import { cleanup, renderHook } from "./testing/renderHook";

afterEach(async () => {
  await cleanup();
  vi.unstubAllGlobals();
});

describe("a store", () => {
  it("tells those listening when its value changes, and not when it is set to what it holds", () => {
    const s = createStore(1), heard = vi.fn();
    s.subscribe(heard);
    s.set(1);
    expect(heard).not.toHaveBeenCalled();
    s.set(2);
    expect(heard).toHaveBeenCalledTimes(1);
    expect(s.get()).toBe(2);
  });
  it("tells a new object apart from the one it holds, even with the same contents", () => {
    const s = createStore({ n: 1 }), heard = vi.fn();
    s.subscribe(heard);
    s.set({ n: 1 });
    expect(heard).toHaveBeenCalledTimes(1);
  });
  it("tells nothing more to a listener that has gone", () => {
    const s = createStore("a"), heard = vi.fn();
    const off = s.subscribe(heard);
    off();
    s.set("b");
    expect(heard).not.toHaveBeenCalled();
  });
  it("gives a component its value, and the new one when it changes", async () => {
    const s = createStore("day");
    const seen = await renderHook(() => s.useValue());
    expect(seen.result).toBe("day");
    await act(async () => s.set("night"));
    expect(seen.result).toBe("night");
  });
});

describe("what a browser keeps", () => {
  function storage(kept: Map<string, string>) {
    vi.stubGlobal("localStorage", {
      getItem: (k: string) => kept.get(k) ?? null,
      setItem: (k: string, v: string) => void kept.set(k, v),
      removeItem: (k: string) => void kept.delete(k),
    });
  }
  const refusing = () => {
    throw new Error("SecurityError");
  };

  it("is read back as it was written, and a null takes it away", () => {
    const kept = new Map<string, string>();
    storage(kept);
    store("mm_x", "1");
    expect(stored("mm_x")).toBe("1");
    store("mm_x", null);
    expect(kept.has("mm_x")).toBe(false);
    expect(stored("mm_x")).toBeNull();
  });
  it("reads as nothing where storage refuses, as in a private window", () => {
    vi.stubGlobal("localStorage", { getItem: refusing, setItem: refusing, removeItem: refusing });
    expect(stored("mm_x")).toBeNull();
    expect(() => store("mm_x", "1")).not.toThrow();
    expect(() => store("mm_x", null)).not.toThrow();
  });
  it("reads as nothing where there is no storage at all", () => {
    vi.stubGlobal("localStorage", undefined);
    expect(stored("mm_x")).toBeNull();
    expect(() => store("mm_x", "1")).not.toThrow();
  });
});

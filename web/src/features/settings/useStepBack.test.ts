import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, renderHook } from "../../lib/testing/renderHook";
import { useStepBack } from "./useStepBack";

type Listener = (e: object) => void;

// Settings open on the page: its keys, heard ahead of anything under them, and what a key pressed did.
function aPage() {
  const capturing = new Set<Listener>(), bubbling = new Set<Listener>();
  const side = (capture?: boolean) => (capture ? capturing : bubbling);
  vi.stubGlobal("document", {
    addEventListener: (_: string, fn: Listener, capture?: boolean) => void side(capture).add(fn),
    removeEventListener: (_: string, fn: Listener, capture?: boolean) => void side(capture).delete(fn),
  });
  const dialog = { focus: vi.fn() };
  const field = { closest: (sel: string) => (sel.includes("input") ? {} : sel.includes("dialog") ? dialog : null), blur: vi.fn() };
  const loose = { closest: (sel: string) => (sel.includes("input") ? {} : null), blur: vi.fn() };
  const elsewhere = { closest: () => null, blur: vi.fn() };
  const press = (key: string, target: object = elsewhere) => {
    const e = { key, target, stopPropagation: vi.fn(), preventDefault: vi.fn() };
    capturing.forEach((fn) => fn(e));
    return { ...e, stopped: e.stopPropagation.mock.calls.length > 0 && e.preventDefault.mock.calls.length > 0 };
  };
  return { press, field, loose, dialog, capturing, bubbling };
}

const settings = (step?: () => boolean) => renderHook(() => useStepBack(step));

afterEach(async () => {
  await cleanup();
  vi.unstubAllGlobals();
});

describe("Esc in Settings", () => {
  it("leaves the field you're typing in first, and goes no further", async () => {
    const page = aPage(), step = vi.fn(() => true);
    await settings(step);
    const esc = page.press("Escape", page.field);
    expect(page.dialog.focus).toHaveBeenCalled();
    expect(esc.stopped).toBe(true);
    expect(step).not.toHaveBeenCalled();
  });
  it("puts the focus on the dialog around the field, where its focus trap leaves it, not nowhere", async () => {
    const page = aPage();
    await settings();
    page.press("Escape", page.field);
    expect(page.dialog.focus).toHaveBeenCalledTimes(1);
    expect(page.field.blur).not.toHaveBeenCalled();
  });
  it("blurs a field with no dialog around it", async () => {
    const page = aPage();
    await settings();
    expect(page.press("Escape", page.loose).stopped).toBe(true);
    expect(page.loose.blur).toHaveBeenCalledTimes(1);
  });
  it("then closes what Settings has open, a form, and goes no further", async () => {
    const page = aPage(), step = vi.fn(() => true);
    await settings(step);
    expect(page.press("Escape").stopped).toBe(true);
    expect(step).toHaveBeenCalledTimes(1);
  });
  it("only then reaches Settings itself, to close it", async () => {
    const page = aPage();
    await settings(() => false);
    expect(page.press("Escape").stopped).toBe(false);
    await cleanup();
    await settings();
    expect(page.press("Escape").stopped).toBe(false);
  });
  it("is heard ahead of what is under Settings", async () => {
    const page = aPage();
    await settings();
    expect(page.capturing.size).toBe(1);
    expect(page.bubbling.size).toBe(0);
  });
});

it("lets every other key through", async () => {
  const page = aPage(), step = vi.fn(() => true);
  await settings(step);
  const enter = page.press("Enter", page.field);
  expect(enter.stopped).toBe(false);
  expect(page.field.blur).not.toHaveBeenCalled();
  expect(step).not.toHaveBeenCalled();
});

it("stops listening when Settings closes", async () => {
  const page = aPage();
  await settings();
  await cleanup();
  expect(page.capturing.size).toBe(0);
});

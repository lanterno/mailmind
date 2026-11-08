import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { PREFERENCE_DEFAULTS } from "./api/limits.gen";

const client = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), refused: { detail: "" } as { detail: string } | null }));
vi.mock("./api/client", () => ({ get: client.get, post: client.post, refusalOf: () => client.refused }));

function browserHolding(held: Record<string, string>) {
  const kept = new Map(Object.entries(held));
  vi.stubGlobal("localStorage", {
    getItem: (key: string) => kept.get(key) ?? null,
    setItem: (key: string, value: string) => void kept.set(key, value),
    removeItem: (key: string) => void kept.delete(key),
  });
  return kept;
}

const load = async () => {
  vi.resetModules();
  return import("./prefs");
};
const OLD = { mm_theme: "dark", mm_nokeys: "1", mm_threadview: "0", mm_searchscope: "account", mm_draft_tone: "warm", mm_mailtheme: "original", mm_read: '{"width":"wide","size":"xl","font":"mono"}' };
const answer = (over: object) => ({ preferences: { ...PREFERENCE_DEFAULTS, ...over } });

beforeEach(() => {
  vi.resetAllMocks();
  client.refused = { detail: "" };
});
afterEach(() => vi.unstubAllGlobals());

describe("what a browser held under its old keys", () => {
  it("comes to the block's words", async () => {
    const { heldUnderOldKeys } = await load();
    expect(heldUnderOldKeys((k) => (OLD as Record<string, string>)[k] ?? null)).toEqual({
      theme: "night", single_keys: false, conversations: false, search_scope: "account", draft_tone: "warm", mail_colors: "original",
      read_width: "wide", read_size: "xl", read_font: "mono",
    });
  });
  it("says nothing of what isn't there or won't read", async () => {
    const { heldUnderOldKeys } = await load();
    expect(heldUnderOldKeys(() => null)).toEqual({});
    expect(heldUnderOldKeys((k) => (k === "mm_read" ? "{nope" : null))).toEqual({});
    expect(heldUnderOldKeys((k) => (k === "mm_read" ? "null" : null))).toEqual({});
  });
});

describe("the page starts", () => {
  it("from the defaults, with nothing held", async () => {
    browserHolding({});
    const { prefs } = await load();
    expect(prefs.get()).toEqual(PREFERENCE_DEFAULTS);
  });
  it("from what the old keys said, so an un-imported browser looks as it did", async () => {
    browserHolding({ mm_theme: "dusk", mm_nokeys: "1" });
    const { prefs } = await load();
    expect(prefs.get().theme).toBe("dusk");
    expect(prefs.get().single_keys).toBe(false);
  });
  it("from the last known copy, and not from a bad one", async () => {
    browserHolding({ mm_prefs: JSON.stringify({ density: "compact" }) });
    expect((await load()).prefs.get().density).toBe("compact");
    browserHolding({ mm_prefs: "{nope" });
    expect((await load()).prefs.get().density).toBe(PREFERENCE_DEFAULTS.density);
  });
});

describe("loading what the server holds", () => {
  it("with no old keys only reads", async () => {
    browserHolding({});
    client.get.mockResolvedValue(answer({ density: "cozy" }));
    const { loadPrefs, prefs } = await load();
    await loadPrefs();
    expect(client.post).not.toHaveBeenCalled();
    expect(prefs.get().density).toBe("cozy");
  });
  it("brings old keys in, once, and lets go of them only then", async () => {
    const kept = browserHolding(OLD);
    client.post.mockResolvedValue(answer({ theme: "night" }));
    const { loadPrefs, prefs } = await load();
    await loadPrefs();
    expect(client.post).toHaveBeenCalledTimes(1);
    expect(client.post.mock.calls[0]![0]).toBe("/api/preferences/import");
    expect(Object.keys(OLD).filter((k) => kept.has(k))).toEqual([]);
    expect(JSON.parse(kept.get("mm_prefs")!).theme).toBe("night");
    expect(prefs.get().theme).toBe("night");
    await loadPrefs();
    expect(client.post).toHaveBeenCalledTimes(1);
    expect(client.get).toHaveBeenCalledTimes(1);
  });
  it("holds on to the old keys when the server didn't take them", async () => {
    const kept = browserHolding(OLD);
    client.post.mockRejectedValue(new Error("no"));
    const { loadPrefs, prefs } = await load();
    await loadPrefs();
    expect(kept.get("mm_theme")).toBe("dark");
    expect(prefs.get().theme).toBe("night");
  });
  it("leaves the last known values when the server can't be reached", async () => {
    browserHolding({ mm_prefs: JSON.stringify({ density: "compact" }) });
    client.get.mockRejectedValue(new Error("down"));
    const { loadPrefs, prefs } = await load();
    await loadPrefs();
    expect(prefs.get().density).toBe("compact");
  });
});

describe("a change", () => {
  it("shows at once and goes to the server alone", async () => {
    const kept = browserHolding({});
    client.post.mockResolvedValue(answer({ density: "cozy" }));
    const { prefs, setPrefs } = await load();
    setPrefs({ density: "cozy" });
    expect(prefs.get().density).toBe("cozy");
    expect(JSON.parse(kept.get("mm_prefs")!).density).toBe("cozy");
    expect(client.post).toHaveBeenCalledWith("/api/preferences", { density: "cozy" });
  });
  it("that is refused goes back to what the server holds", async () => {
    browserHolding({});
    client.post.mockRejectedValue(new Error("no"));
    client.get.mockResolvedValue(answer({}));
    const { prefs, setPrefs } = await load();
    setPrefs({ density: "cozy" });
    await vi.waitFor(() => expect(prefs.get().density).toBe(PREFERENCE_DEFAULTS.density));
  });
});

describe("one preference as a store", () => {
  it("reads and sets just its own", async () => {
    browserHolding({});
    client.post.mockResolvedValue(answer({ conversations: false }));
    const { pref } = await load();
    const conversations = pref("conversations");
    expect(conversations.get()).toBe(true);
    conversations.set(false);
    expect(conversations.get()).toBe(false);
    expect(client.post).toHaveBeenCalledWith("/api/preferences", { conversations: false });
  });
});

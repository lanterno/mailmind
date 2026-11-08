// What a person has set, which the server keeps (the "preferences" block of mailmind.config.json), so it outlives a browser-data
// clear. This browser holds the last known copy under one key: a page starts from it, and theme.js paints the first frame from it.
// What this browser used to hold under its own keys comes in once and is gone. The server's answer is what stands.
import { useSyncExternalStore } from "react";
import { get, post, refusalOf } from "./api/client";
import { PREFERENCE_DEFAULTS } from "./api/limits.gen";
import type { GetAnswers, PostBodies } from "./api/types";
import { createStore, store, stored, type Store } from "./store";
import { toast } from "./toast";

export type Prefs = GetAnswers["/api/preferences"]["preferences"];
type Changes = PostBodies["/api/preferences"];

const COPY = "mm_prefs";

// What each old key said, in the block's words. The server drops what can't be a preference; until it has answered, the ones with
// words of their own below (`readPrefs`) read defensively as they always did.
const OLD: Record<string, (said: string) => Partial<Prefs>> = {
  mm_theme: (said) => ({ theme: said === "dark" ? "night" : said === "dusk" ? "dusk" : "day" }),
  mm_mailtheme: (said) => ({ mail_colors: said as Prefs["mail_colors"] }),
  mm_nokeys: (said) => ({ single_keys: said !== "1" }),
  mm_threadview: (said) => ({ conversations: said !== "0" }),
  mm_searchscope: (said) => ({ search_scope: said === "account" ? "account" : "all" }),
  mm_draft_tone: (said) => ({ draft_tone: said as Prefs["draft_tone"] }),
  mm_read: (said) => {
    try {
      const was = JSON.parse(said) as { width?: unknown; size?: unknown; font?: unknown } | null;
      return { read_width: was?.width as Prefs["read_width"], read_size: was?.size as Prefs["read_size"], read_font: was?.font as Prefs["read_font"] };
    } catch {
      return {};
    }
  },
};

export function heldUnderOldKeys(said: (key: string) => string | null): Partial<Prefs> {
  const held: Partial<Prefs> = {};
  for (const [key, read] of Object.entries(OLD)) {
    const value = said(key);
    if (value !== null) Object.assign(held, read(value));
  }
  return Object.fromEntries(Object.entries(held).filter(([, value]) => value !== undefined)) as Partial<Prefs>;
}

function lastKnown(): Partial<Prefs> {
  try {
    return (JSON.parse(stored(COPY) ?? "{}") as Partial<Prefs> | null) ?? {};
  } catch {
    return {};
  }
}

export const prefs = createStore<Prefs>({ ...PREFERENCE_DEFAULTS, ...lastKnown(), ...heldUnderOldKeys(stored) });

function showing(p: Prefs) {
  if (typeof document === "undefined") return;     // a test with no page
  const body = document.body;
  body.classList.toggle("theme-light", p.theme !== "night");
  body.classList.toggle("theme-dusk", p.theme === "dusk");
  body.classList.toggle("hc", p.high_contrast);
  body.dataset["density"] = p.density;
}

function settle(p: Prefs) {
  prefs.set(p);
  store(COPY, JSON.stringify(p));
  showing(p);
}
showing(prefs.get());

// Change what this names: on the page at once, on the server right after. A value the server refuses puts back what it holds.
export function setPrefs(changes: Changes, saved?: () => void) {
  settle({ ...prefs.get(), ...changes } as Prefs);
  post("/api/preferences", changes).then(() => saved?.(), (e: unknown) => {
    toast(refusalOf(e)?.detail || "Couldn’t save that setting");
    void loadPrefs();
  });
}

// One preference as a store of its own: its readers re-render only when it changes.
export function pref<K extends keyof Prefs>(key: K): Store<Prefs[K]> {
  const read = () => prefs.get()[key];
  return {
    get: read,
    set: (value) => setPrefs({ [key]: value } as Changes),
    subscribe: prefs.subscribe,
    useValue: () => useSyncExternalStore(prefs.subscribe, read),
  };
}

export const singleKeys = pref("single_keys");

const minutes = (time: string) => Number(time.slice(0, 2)) * 60 + Number(time.slice(3));

// Whether quiet hours cover this moment, by this computer's clock. A span that crosses midnight is that evening and the morning
// after; one that starts where it ends covers nothing.
export function quietNow(at: Date, p: Pick<Prefs, "quiet_hours" | "quiet_from" | "quiet_until">) {
  if (!p.quiet_hours || p.quiet_from === p.quiet_until) return false;
  const now = at.getHours() * 60 + at.getMinutes(), from = minutes(p.quiet_from), until = minutes(p.quiet_until);
  return from < until ? now >= from && now < until : now >= from || now < until;
}

// Whether a desktop notification of this kind may go now. What else a person hears of it (the title's count, the pill) isn't asked.
export function mayNotify(kind: "notify_new_mail" | "notify_meetings", at = new Date()) {
  const p = prefs.get();
  return p[kind] && !quietNow(at, p);
}

// As the page loads, once. What this browser still holds under its old keys goes to the server first, for what the server doesn't
// say yet; only an answer from the server lets go of them, so a failed try comes round again on the next page load.
export async function loadPrefs() {
  const old = Object.keys(OLD).filter((key) => stored(key) !== null);
  try {
    const answer = old.length ? await post("/api/preferences/import", { held: heldUnderOldKeys(stored) }) : await get("/api/preferences");
    old.forEach((key) => store(key, null));
    settle(answer.preferences);
  } catch { /* the last known values stand, and the next load asks again */ }
}

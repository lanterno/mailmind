// Reading settings (Aa). Width is the reading measure; face and size typeset plain-text mail only, since rich mail keeps
// the sender's own type. System stacks, since a mail frame's policy can't load the app's fonts. Kept under the keys the
// current page uses.
import { createStore, store, stored } from "../../lib/store";

export const WIDTHS = { narrow: 640, medium: 760, wide: 920 } as const;
export const SIZES = { s: 15, m: 16.5, l: 18, xl: 20 } as const;
export const FONTS = {
  sans: '-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif',
  serif: '"Iowan Old Style",Charter,"Source Serif 4",Georgia,serif',
  mono: 'ui-monospace,"SF Mono",Menlo,Consolas,monospace',
} as const;

export interface ReadPrefs { width: keyof typeof WIDTHS; size: keyof typeof SIZES; font: keyof typeof FONTS; colors: "auto" | "dark" | "original" }

const pick = <T extends object>(from: T, key: unknown, fallback: keyof T): keyof T =>
  typeof key === "string" && key in from ? (key as keyof T) : fallback;

export function parsePrefs(saved: string | null, colors: string | null): ReadPrefs {
  let p: Record<string, unknown> = {};
  try {
    p = (JSON.parse(saved ?? "{}") as Record<string, unknown> | null) ?? {};
  } catch { /* a bad value is no setting */ }
  return {
    width: pick(WIDTHS, p["width"], "medium"),
    size: pick(SIZES, p["size"], "m"),
    font: pick(FONTS, p["font"], "sans"),
    colors: colors === "dark" || colors === "original" ? colors : "auto",
  };
}

export const readPrefs = createStore<ReadPrefs>(parsePrefs(stored("mm_read"), stored("mm_mailtheme")));

export function setReadPref<K extends keyof ReadPrefs>(key: K, value: ReadPrefs[K]) {
  const next = { ...readPrefs.get(), [key]: value };
  readPrefs.set(next);
  if (key === "colors") store("mm_mailtheme", value === "auto" ? null : String(value));
  else store("mm_read", JSON.stringify({ width: next.width, size: next.size, font: next.font }));
}

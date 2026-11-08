// Reading settings (Aa). Width is the reading measure; face and size typeset plain-text mail only, since rich mail keeps
// the sender's own type. System stacks, since a mail frame's policy can't load the app's fonts. Kept with the rest of the
// preferences (lib/prefs.ts).
import { useSyncExternalStore } from "react";
import type { ReadFont, ReadSize, ReadWidth } from "../../lib/api/types";
import { prefs, setPrefs, type Prefs } from "../../lib/prefs";

export const WIDTHS: Record<ReadWidth, number> = { narrow: 640, medium: 760, wide: 920 };
export const SIZES: Record<ReadSize, number> = { s: 15, m: 16.5, l: 18, xl: 20 };
export const FONTS: Record<ReadFont, string> = {
  sans: '-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif',
  serif: '"Iowan Old Style",Charter,"Source Serif 4",Georgia,serif',
  mono: 'ui-monospace,"SF Mono",Menlo,Consolas,monospace',
};

export interface ReadPrefs { width: ReadWidth; size: ReadSize; font: ReadFont; colors: Prefs["mail_colors"] }

const pick = <K extends string>(from: Record<K, unknown>, key: unknown, fallback: K): K =>
  typeof key === "string" && Object.hasOwn(from, key) ? (key as K) : fallback;

// What the store holds, but for an old key a browser still holds until the server has answered: a value it doesn't know is no setting.
export function fromPrefs(p: Prefs): ReadPrefs {
  return {
    width: pick(WIDTHS, p.read_width, "medium"),
    size: pick(SIZES, p.read_size, "m"),
    font: pick(FONTS, p.read_font, "sans"),
    colors: p.mail_colors === "dark" || p.mail_colors === "original" ? p.mail_colors : "auto",
  };
}

let made: { of: Prefs; value: ReadPrefs } | undefined;
const current = (): ReadPrefs => {
  const of = prefs.get();
  if (made?.of !== of) made = { of, value: fromPrefs(of) };
  return made.value;
};

export const readPrefs = { get: current, useValue: () => useSyncExternalStore(prefs.subscribe, current) };

const NAMED: { [K in keyof ReadPrefs]: "read_width" | "read_size" | "read_font" | "mail_colors" } = { width: "read_width", size: "read_size", font: "read_font", colors: "mail_colors" };

export function setReadPref<K extends keyof ReadPrefs>(key: K, value: ReadPrefs[K]) {
  setPrefs({ [NAMED[key]]: value });
}

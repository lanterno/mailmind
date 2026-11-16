// The light the page is painted in, from the theme a person chose. Automatic (#44) is Night while this computer is in dark mode,
// and else the time of day's: Day from 6, Dusk from 17, Night from 21, as the Mac app's sky goes. assets/theme.js paints the first
// frame on the same terms, so change the hours there too.
import type { Theme } from "./api/types";

export type Light = Exclude<Theme, "auto">;

export function lightAt(dark: boolean, hour: number): Light {
  if (dark || hour >= 21 || hour < 6) return "night";
  return hour >= 17 ? "dusk" : "day";
}

// Whether this computer is in dark mode now: no for a test with no window.
export function systemDark(): boolean {
  return typeof window !== "undefined" && typeof window.matchMedia === "function" && window.matchMedia("(prefers-color-scheme: dark)").matches;
}

export function lightOf(theme: Theme, dark = systemDark(), at = new Date()): Light {
  return theme === "auto" ? lightAt(dark, at.getHours()) : theme;
}

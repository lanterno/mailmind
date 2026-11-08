// The three themes, as the preferences keep them. What each looks like is CSS; lib/prefs.ts sets the classes on the same terms
// theme.js does before the first paint.
import type { Theme } from "./api/types";
import { pref } from "./prefs";

export type ThemeName = Theme;
export const THEME_LABEL: Record<ThemeName, string> = { day: "Day", dusk: "Dusk", night: "Night" };
const ORDER: ThemeName[] = ["day", "dusk", "night"];

export const theme = pref("theme");
export const nextTheme = (t: ThemeName): ThemeName => ORDER[(ORDER.indexOf(t) + 1) % ORDER.length]!;

export const setTheme = (t: ThemeName) => theme.set(t);

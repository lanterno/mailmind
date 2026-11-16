// The themes a person chooses between, as the preferences keep them: Automatic, or one light. What each light looks like is CSS;
// lib/prefs.ts sets the classes on the same terms theme.js does before the first paint, and lib/light.ts says Automatic's.
import type { Theme } from "./api/types";
import { pref } from "./prefs";

export type ThemeName = Theme;
export const THEME_LABEL: Record<ThemeName, string> = { auto: "Automatic", day: "Day", dusk: "Dusk", night: "Night" };
const ORDER: ThemeName[] = ["auto", "day", "dusk", "night"];

export const theme = pref("theme");
export const nextTheme = (t: ThemeName): ThemeName => ORDER[(ORDER.indexOf(t) + 1) % ORDER.length]!;

export const setTheme = (t: ThemeName) => theme.set(t);

// What a choice is called when it is said alone, as a toast says it: "Day theme", "Automatic light".
export const themeSaid = (t: ThemeName) => (t === "auto" ? "Automatic light" : `${THEME_LABEL[t]} theme`);

// The three themes, on the same classes and the same storage key theme.js reads before the first paint.
import { createStore, store } from "./store";

export type ThemeName = "day" | "dusk" | "night";
export const THEME_LABEL: Record<ThemeName, string> = { day: "Day", dusk: "Dusk", night: "Night" };
const ORDER: ThemeName[] = ["day", "dusk", "night"];

function read(): ThemeName {
  const body = document.body.classList;
  return !body.contains("theme-light") ? "night" : body.contains("theme-dusk") ? "dusk" : "day";
}

export const theme = createStore<ThemeName>(read());
export const nextTheme = (t: ThemeName): ThemeName => ORDER[(ORDER.indexOf(t) + 1) % ORDER.length]!;

export function setTheme(t: ThemeName) {
  document.body.classList.toggle("theme-light", t !== "night");
  document.body.classList.toggle("theme-dusk", t === "dusk");
  store("mm_theme", t === "night" ? "dark" : t === "dusk" ? "dusk" : "light");
  theme.set(t);
}

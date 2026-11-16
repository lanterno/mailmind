import { createStore } from "./store";

// Settings' sections, each a tab of its own.
export type SettingsSection = "connections" | "preferences" | "ai";

export const settingsOpen = createStore(false);
export const settingsSection = createStore<SettingsSection>("connections");

// Settings, open at a section: its connections unless another is named, as a link to AI's set-up names AI.
export function openSettings(section: SettingsSection = "connections") {
  settingsSection.set(section);
  settingsOpen.set(true);
}

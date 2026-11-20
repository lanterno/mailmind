// The server's snooze presets, in the menu's order and words, as they stand at a moment. Later today is three hours
// on, on the hour, unless that runs past midnight (from 21:00), when the server snoozes to 8 the next morning instead,
// as no one wants waking at two: the menu says so then.
import type { SnoozePreset } from "../../lib/api/types";
import { entries } from "../../lib/entries";

// Whether Later today snoozes to the next morning at `now`, by the clocks here, as the server reads them.
export function laterTodayIsTomorrow(now: Date): boolean {
  return new Date(now.getTime() + 3 * 3600 * 1000).toDateString() !== now.toDateString();
}

export const snoozePresets = (now: Date) =>
  entries<SnoozePreset, string>({
    later_today: laterTodayIsTomorrow(now) ? "Tomorrow morning" : "Later today",
    tomorrow: "Tomorrow 8am",
    weekend: "This weekend",
    next_week: "Next week",
  });

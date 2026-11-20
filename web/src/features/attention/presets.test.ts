import { describe, expect, it } from "vitest";
import { laterTodayIsTomorrow, snoozePresets } from "./presets";

// Local times, as the menu reads the clock: 20 November 2026 at `hour`:`minute`.
const at = (hour: number, minute = 0) => new Date(2026, 10, 20, hour, minute);

describe("the snooze presets", () => {
  it("say Later today while three hours on is still today", () => {
    expect(laterTodayIsTomorrow(at(20, 59))).toBe(false);
    expect(snoozePresets(at(9)).map(([, words]) => words)).toEqual(["Later today", "Tomorrow 8am", "This weekend", "Next week"]);
    expect(snoozePresets(at(0, 30))[0]).toEqual(["later_today", "Later today"]);
  });

  it("say what Later today does once it runs past midnight, as the server snoozes it to the next morning", () => {
    expect(laterTodayIsTomorrow(at(21))).toBe(true);
    expect(snoozePresets(at(21))[0]).toEqual(["later_today", "Tomorrow morning"]);
    expect(snoozePresets(at(23, 30))[0]).toEqual(["later_today", "Tomorrow morning"]);
  });

  it("keep their order, so each digit picks the same preset all day", () => {
    expect(snoozePresets(at(22)).map(([preset]) => preset)).toEqual(["later_today", "tomorrow", "weekend", "next_week"]);
  });
});

import { describe, expect, it } from "vitest";
import { lightAt, lightOf } from "./light";

describe("the Automatic light (#44)", () => {
  it("is Night whenever this computer is in dark mode", () => {
    for (const hour of [0, 6, 12, 17, 21]) expect(lightAt(true, hour)).toBe("night");
  });
  it("otherwise goes by the time of day: Day from 6, Dusk from 17, Night from 21", () => {
    const by = Object.fromEntries([0, 5, 6, 12, 16, 17, 20, 21, 23].map((hour) => [hour, lightAt(false, hour)]));
    expect(by).toEqual({ 0: "night", 5: "night", 6: "day", 12: "day", 16: "day", 17: "dusk", 20: "dusk", 21: "night", 23: "night" });
  });
  it("leaves a light a person chose alone, dark mode or not", () => {
    const noon = new Date(2026, 10, 2, 12), evening = new Date(2026, 10, 2, 18);
    expect(lightOf("auto", false, noon)).toBe("day");
    expect(lightOf("auto", false, evening)).toBe("dusk");
    expect(lightOf("auto", true, noon)).toBe("night");
    expect(lightOf("dusk", true, noon)).toBe("dusk");
    expect(lightOf("day", true, evening)).toBe("day");
  });
});

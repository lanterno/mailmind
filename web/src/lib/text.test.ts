import { expect, it } from "vitest";
import { avatarHue, dayGroup, fileSize, initialsOf, shortAddress, usd } from "./text";

it("takes initials from capitalised words first", () => {
  expect(initialsOf("Library of Ash Hollow", "x@example.test")).toBe("LA");
  expect(initialsOf("Lin Okafor", "")).toBe("LO");
  expect(initialsOf("lin", "")).toBe("L");
});
it("falls back to the address, and to a question mark", () => {
  expect(initialsOf("", "bo@example.test")).toBe("B");
  expect(initialsOf("bo@example.test", "bo@example.test")).toBe("B");
  expect(initialsOf("", "")).toBe("?");
});
it("gives a stable hue on the wheel", () => {
  expect(avatarHue("lin@example.test")).toBe(avatarHue("lin@example.test"));
  expect(avatarHue("lin@example.test")).toBeLessThan(360);
  expect(avatarHue(null)).toBe(0);
});
it("says a file's size", () => {
  expect([fileSize(0), fileSize(512), fileSize(2048), fileSize(3 * 1048576)]).toEqual(["", "512 B", "2 KB", "3.0 MB"]);
});
it("sorts a message into a day's section", () => {
  const now = new Date(2026, 10, 6, 12);
  const at = (d: number) => new Date(2026, 10, d, 8).getTime() / 1000;
  expect([dayGroup(at(6), now), dayGroup(at(5), now), dayGroup(at(1), now), dayGroup(at(6) - 40 * 86400, now)])
    .toEqual(["Today", "Yesterday", "Past week", "Earlier"]);
});
it("shortens an address to its name", () => expect(shortAddress("bo@example.test")).toBe("bo"));
it("says money rounded up to the cent", () => {
  expect([usd(0), usd(1), usd(15_000_000), usd(1_234_567_890)]).toEqual(["$0.00", "$0.01", "$15.00", "$1,234.57"]);
});

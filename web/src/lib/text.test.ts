import { describe, expect, it } from "vitest";
import { avatarHue, calling, dayGroup, fileSize, initialsOf, shortAddress, usd, relWhen } from "./text";

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
  // A database grows past a gigabyte.
  expect([fileSize(1023 * 1048576), fileSize(1.5 * 1073741824)]).toEqual(["1023.0 MB", "1.5 GB"]);
});
it("sorts a message into a day's section", () => {
  const now = new Date(2026, 10, 6, 12);
  const at = (d: number) => new Date(2026, 10, d, 8).getTime() / 1000;
  expect([dayGroup(at(6), now), dayGroup(at(5), now), dayGroup(at(1), now), dayGroup(at(6) - 40 * 86400, now)])
    .toEqual(["Today", "Yesterday", "Past week", "Earlier"]);
});
it("shortens an address to its name", () => expect(shortAddress("bo@example.test")).toBe("bo"));

describe("what a short line calls someone", () => {
  it("calls a person by their first name, their address or their name's shape saying so", () => {
    expect(calling("Ren Mori", "ren@example.test")).toBe("Ren");
    expect(calling("Ren Mori", "rmori@example.test")).toBe("Ren");
    expect(calling("Ren Mori", "mori.r@example.test")).toBe("Ren");
    expect(calling("Zoë Ångström", "zoe.angstrom@example.test")).toBe("Zoë");
    expect(calling("Ren Mori")).toBe("Ren");
    expect(calling("Lin")).toBe("Lin");
  });
  it("says an organisation's name whole", () => {
    expect(calling("Parcel Service", "parcel@example.test")).toBe("Parcel Service");
    expect(calling("Mori, Ren", "ren@example.test")).toBe("Mori, Ren");
    expect(calling("Ada Park", "orders@example.test")).toBe("Ada Park");
    expect(calling("Ada Park", "noreply-ada@example.test")).toBe("Ada Park");
    expect(calling("Studio 54")).toBe("Studio 54");
    expect(calling("the loom weekly")).toBe("the loom weekly");
    // Its address carries the last word of its name, as a person's may carry theirs: never "Guild".
    expect(calling("Guild Gazette", "gazette@example.test")).toBe("Guild Gazette");
    expect(calling("Guild Gazette", "gazette@guild.example.test")).toBe("Guild Gazette");
  });
  it("says a name whose address doesn't carry it whole", () => {
    expect(calling("Ren Mori", "x9@example.test")).toBe("Ren Mori");
    // The last name alone is no sign of a person, as gazette@ for "Guild Gazette" isn't.
    expect(calling("Ren Mori", "mori@example.test")).toBe("Ren Mori");
  });
  it("has nothing to call one with only an address", () => {
    expect([calling(null), calling(""), calling(" \"\" "), calling("ren@example.test", "ren@example.test")]).toEqual([null, null, null, null]);
  });
  it("takes a name's quotes off", () => expect(calling("'Lin'")).toBe("Lin"));
});
it("says money rounded up to the cent", () => {
  expect([usd(0), usd(1), usd(15_000_000), usd(1_234_567_890)]).toEqual(["$0.00", "$0.01", "$15.00", "$1,234.57"]);
});

describe("how long ago", () => {
  const now = Date.parse("2026-11-02T12:00:00Z");
  const ago = (s: number) => new Date(now - s * 1000).toISOString();
  it.each([[10, "just now"], [120, "2m ago"], [7200, "2h ago"], [3 * 86400, "3d ago"]])("%i seconds is %s", (s, words) => expect(relWhen(ago(s), now)).toBe(words));
  it("says nothing of what isn't a time", () => expect(relWhen("soon", now)).toBe(""));
  it("reads a time ahead as now", () => expect(relWhen(ago(-60), now)).toBe("just now"));
});

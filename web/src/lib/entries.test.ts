import { describe, expect, expectTypeOf, it } from "vitest";
import { entries } from "./entries";

describe("entries", () => {
  it("keeps a record's order, and its keys' type", () => {
    const said = entries<"b" | "a", string>({ b: "Bee", a: "Ay" });
    expect(said).toEqual([["b", "Bee"], ["a", "Ay"]]);
    expectTypeOf(said).toEqualTypeOf<["b" | "a", string][]>();
  });
});

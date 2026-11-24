import { describe, expect, it } from "vitest";
import { accountColour } from "./accountColour";

const ADA = "ada@example.test", BEA = "bea@example.test", CY = "cy@example.test";

describe("accountColour", () => {
  it("wears the place the server keeps, so removing an account moves no other's", () => {
    expect(accountColour({ [ADA]: 0, [CY]: 2 }, [ADA, CY], CY)).toBe("var(--acct-3)");
  });

  it("wraps a place past the palette's last colour", () => {
    expect(accountColour({ [BEA]: 7 }, [BEA], BEA)).toBe("var(--acct-2)");
  });

  it("goes by the account's order where no place is kept", () => {
    expect(accountColour({}, [ADA, BEA], BEA)).toBe("var(--acct-2)");
    expect(accountColour({}, [ADA], CY)).toBe("var(--acct-1)");
  });
});

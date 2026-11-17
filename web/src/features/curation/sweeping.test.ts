import { afterEach, describe, expect, it } from "vitest";
import { PLANNING, sweeps, sweepingWords, sweepWhile } from "./sweeping";

function deferred() {
  let resolve!: () => void, reject!: (e: unknown) => void;
  const promise = new Promise<void>((a, b) => { resolve = a; reject = b; });
  return { promise, resolve, reject };
}

const ADA = "ada@example.test", BEA = "bea@example.test";

afterEach(() => sweeps.set(new Map()));

describe("an account's sweep while it runs", () => {
  it("has its row say what runs: a plan, or the sweep and how much it moves", () => {
    expect(PLANNING).toBe("Looking at what a sweep would move…");
    expect(sweepingWords(12)).toBe("Sweeping 12 to Trash…");
  });

  it("is what the row says until it ends, and then nothing", async () => {
    const gate = deferred();
    const running = sweepWhile(ADA, PLANNING, () => gate.promise);
    expect(sweeps.get().get(ADA)).toBe(PLANNING);
    gate.resolve();
    expect(await running).toBe(true);
    expect(sweeps.get().has(ADA)).toBe(false);
  });

  it("asks nothing more of the account while a plan or a sweep runs for it: a second press does nothing", async () => {
    const gate = deferred();
    let asked = 0;
    const first = sweepWhile(ADA, PLANNING, async () => { asked++; await gate.promise; });
    expect(await sweepWhile(ADA, sweepingWords(3), async () => { asked++; })).toBe(false);
    expect(await sweepWhile(ADA, PLANNING, async () => { asked++; })).toBe(false);
    expect(asked).toBe(1);
    expect(sweeps.get().get(ADA)).toBe(PLANNING);
    gate.resolve();
    await first;
    expect(await sweepWhile(ADA, sweepingWords(3), async () => { asked++; })).toBe(true);
    expect(asked).toBe(2);
  });

  it("leaves another account free to sweep", async () => {
    const gate = deferred();
    const first = sweepWhile(ADA, sweepingWords(4), () => gate.promise);
    const other = deferred();
    const second = sweepWhile(BEA, PLANNING, () => other.promise);
    expect([...sweeps.get()]).toEqual([[ADA, "Sweeping 4 to Trash…"], [BEA, PLANNING]]);
    gate.resolve();
    other.resolve();
    expect([await first, await second]).toEqual([true, true]);
    expect(sweeps.get().size).toBe(0);
  });

  it("ends when what runs fails, so Sweep can be pressed again", async () => {
    await expect(sweepWhile(ADA, PLANNING, async () => { throw new Error("offline"); })).rejects.toThrow("offline");
    expect(sweeps.get().has(ADA)).toBe(false);
  });
});

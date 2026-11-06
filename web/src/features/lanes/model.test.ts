import { describe, expect, it } from "vitest";
import type { Row } from "../mailbox/rows";
import { extractAmount, fmtMoney, isRefund, ledger, monthName, parseMoney } from "./model";

const row = (subject: string, date = "2026-11-02", snippet = "") => ({ pk: 1, subject, snippet, date }) as unknown as Row;

describe("reading money", () => {
  it.each([
    ["$1,234.50", "$", 1234.5],
    ["€1.234,50", "€", 1234.5],
    ["£12,50", "£", 12.5],
    ["1,234 USD", "$", 1234],
    ["99.00 EGP", "E£", 99],
    ["45 chf", "CHF", 45],
  ])("reads %s", (s, cur, val) => expect(parseMoney(s)).toEqual({ cur, val }));

  it("takes the largest amount, since receipts repeat the total", () => {
    expect(extractAmount({ subject: "Paid $12.00", snippet: "Tax $2.00, total $14.00" })).toEqual({ cur: "$", val: 14 });
  });
  it("finds nothing where there's no amount", () => {
    expect(extractAmount({ subject: "Your order 1042", snippet: "shipped today" })).toBeNull();
  });
  it("knows a refund by its subject's words", () => {
    expect(isRefund("Your refund is on its way")).toBe(true);
    expect(isRefund("Credit note 22")).toBe(true);
    expect(isRefund("Refundable deposit")).toBe(false);
  });
  it("writes a negative with a real minus", () => expect(fmtMoney("$", -3.5)).toBe("−$3.50"));
  it("names a month, and says undated otherwise", () => {
    expect(monthName("2026-03")).toBe("March 2026");
    expect(monthName("")).toBe("Undated");
  });
});

describe("the ledger", () => {
  it("groups by month as the rows come, with a subtotal per currency and refunds taken off", () => {
    const months = ledger([row("Paid $20.00"), row("Refund of $5.00"), row("Paid €10.00"), row("No amount"), row("Paid $1.50", "2026-10-30")]);
    expect(months.map((m) => m.ym)).toEqual(["2026-11", "2026-10"]);
    expect(months[0]!.lines.map((l) => l.shown)).toEqual([20, -5, 10, null]);
    expect(months[0]!.totals).toEqual([["$", 15], ["€", 10]]);
    expect(months[1]!.totals).toEqual([["$", 1.5]]);
  });
  it("is empty for no rows", () => expect(ledger([])).toEqual([]));
});

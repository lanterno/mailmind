// Paper Trail: a local ledger read out of subjects and snippets. No AI and no network, so it never needs either.
import type { Row } from "../mailbox/rows";

const MONEY = /(?:[$€£]\s?\d[\d.,]*\d|\d[\d.,]*\d\s?(?:USD|EUR|GBP|EGP|CHF|AED))/gi;
const SYMBOL: Record<string, string> = { USD: "$", EUR: "€", GBP: "£", EGP: "E£", CHF: "CHF", AED: "AED" };
const MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];

export interface Amount { cur: string; val: number }

export function parseMoney(s: string): { cur: string; val: number | null } {
  const sym = s.match(/[$€£]/), code = s.match(/USD|EUR|GBP|EGP|CHF|AED/i);
  const cur = sym ? sym[0] : code ? SYMBOL[code[0].toUpperCase()]! : "";
  let num = s.replace(/[^\d.,]/g, "");
  if (num.includes(",") && num.includes(".")) num = num.lastIndexOf(",") > num.lastIndexOf(".") ? num.replace(/\./g, "").replace(",", ".") : num.replace(/,/g, "");
  else if (num.includes(",")) num = /,\d{2}$/.test(num) ? num.replace(",", ".") : num.replace(/,/g, "");
  const val = parseFloat(num);
  return { cur, val: Number.isNaN(val) ? null : val };
}

// The most prominent amount: receipts repeat the total, so the largest.
export function extractAmount(it: Pick<Row, "subject" | "snippet">): Amount | null {
  const found = `${it.subject || ""}  ${it.snippet || ""}`.slice(0, 400).match(MONEY);
  let best: Amount | null = null;
  for (const raw of found ?? []) {
    const { cur, val } = parseMoney(raw);
    if (val !== null && (!best || val > best.val)) best = { cur, val };
  }
  return best;
}

export const isRefund = (subject: string) => /\b(refund|credit note|reimburs|money back)\b/i.test(subject || "");

export function fmtMoney(cur: string, val: number): string {
  return `${val < 0 ? "−" : ""}${cur || ""}${Math.abs(val).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

export function monthName(ym: string): string {
  if (!/^\d{4}-\d{2}$/.test(ym || "")) return "Undated";
  const [y, m] = ym.split("-");
  return `${MONTHS[Number(m) - 1]} ${y}`;
}

export interface LedgerLine { row: Row; amount: Amount | null; refund: boolean; shown: number | null }
export interface LedgerMonth { ym: string; lines: LedgerLine[]; totals: [string, number][] }

// By month as the rows come, newest first, with a subtotal for each currency.
export function ledger(rows: Row[]): LedgerMonth[] {
  const months: (LedgerMonth & { sums: Map<string, number> })[] = [];
  for (const row of rows) {
    const ym = (row.date ?? "").slice(0, 7);
    let at = months[months.length - 1];
    if (!at || at.ym !== ym) months.push(at = { ym, lines: [], totals: [], sums: new Map() });
    const amount = extractAmount(row), refund = Boolean(amount) && isRefund(row.subject);
    const shown = amount ? (refund ? -amount.val : amount.val) : null;
    if (amount) at.sums.set(amount.cur, (at.sums.get(amount.cur) ?? 0) + shown!);
    at.lines.push({ row, amount, refund, shown });
  }
  return months.map(({ sums, ...m }) => ({ ...m, totals: [...sums] }));
}

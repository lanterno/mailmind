// What the Screener says of a decision on a sender.
import type { Verdict } from "../../lib/api/types";
import { calling } from "../../lib/text";

/** "Allowed Ren", "Screened out Thimble & Co · trashed": the sender as the page calls one elsewhere (a person by their
 * first name, an organisation whole), else their whole address, never its part before the @ alone. The Mac's
 * `Screener.calling` says the same. */
export function decisionWords(verdict: Verdict, name: string | null | undefined, address: string, trashed: boolean): string {
  const who = calling(name, address) ?? address;
  return verdict === "keep" ? `Allowed ${who}` : `Screened out ${who}${trashed ? " · trashed" : ""}`;
}

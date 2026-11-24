// What a sender's cleanup says of what Undo takes back, and of the way out its list offers.

import type { UnsubMethod } from "../../lib/api/types";

const WAY_OUT: Record<UnsubMethod, string | null> = { "one-click": "1-click", mailto: "by email", "http-page": "web page", none: null };

/** What a sender's row in Curate says of its way out, as its newest mail stored names it (`unsub_method`): in one click,
 * by an email sent from your account, on a page of theirs, or nothing for a list that offers none. Where none of its
 * headers were read, a newsletter (`lu_likely`) most likely offers one, and the row says no more than that: a `mailto`
 * way out is never called one click. The Mac's `CurateModel.unsubscribeChip` says the same. */
export const wayOutChip = (method: UnsubMethod | null | undefined, likely: boolean): string | null =>
  method ? WAY_OUT[method] : likely ? "newsletter" : null;

/** Whether a cleanup sends an unsubscribe, which can't be undone: one the box asks for, by one click or an email. A page
 * opened in a new tab sends nothing; the person unsubscribes there or not. The Mac's `Cleaning.sendsUnsubscribe` says
 * the same. */
export const sendsUnsubscribe = (method: string, unsubscribe: boolean) =>
  unsubscribe && (method === "one-click" || method === "mailto");

// What a sender's cleanup says of what Undo takes back.

/** Whether a cleanup sends an unsubscribe, which can't be undone: one the box asks for, by one click or an email. A page
 * opened in a new tab sends nothing; the person unsubscribes there or not. The Mac's `Cleaning.sendsUnsubscribe` says
 * the same. */
export const sendsUnsubscribe = (method: string, unsubscribe: boolean) =>
  unsubscribe && (method === "one-click" || method === "mailto");

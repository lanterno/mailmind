// What the digest's rows come to, with no DOM: which of them need you, which can be filed, a topic's mail, and each as a
// list row. The digest's rows say what the AI's verdict asks of you; this only sorts them.
import type { GetAnswers } from "../../lib/api/types";
import { copyId, type Copy } from "../../lib/copy";
import type { Row } from "./rows";

export type DigestItem = GetAnswers["/api/digest"]["items"][number];

export const needsYou = (items: DigestItem[]) =>
  items.filter((it) => it.needs_you).sort((a, b) => (b.importance || 0) - (a.importance || 0) || b.key - a.key);

export const safeToFile = (items: DigestItem[]) => items.filter((it) => it.safe_to_file);

// A group names its mail by copy, and can name mail the digest no longer holds.
export function topicItems(copies: Copy[], items: DigestItem[]): DigestItem[] {
  const by = new Map(items.map((x) => [copyId(x), x]));
  return copies.flatMap((c) => by.get(copyId(c)) ?? []);
}

export function fromDigest(it: DigestItem): Row {
  return {
    id: copyId(it),
    key: it.key,
    uid: it.uid,
    account: it.account,
    folder: it.folder,
    fromName: it.from_name,
    fromAddr: it.from_addr,
    subject: it.subject,
    snippet: "",
    when: it.when,
    date: "",
    ts: null,        // sorted by what matters, so no day sections
    seen: Boolean(it.seen),
    flagged: Boolean(it.flagged),
    hasAttach: Boolean(it.has_attach),
    isJunk: false,
    replyWorthy: Boolean(it.reply_worthy),
    bucket: it.bucket,
    threadId: it.thread_id ?? "",
    count: 1,
    threadCount: 1,
    action: String(it.action ?? "").trim(),
    summary: it.summary ?? "",
    needsYou: Boolean(it.needs_you),
  };
}

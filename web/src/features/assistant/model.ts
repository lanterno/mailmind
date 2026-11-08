// What the brief works out from the digest and the clock, with no DOM. The digest's rows say what the AI's verdict asks
// of you; this only sorts and words them.
import type { GetAnswers } from "../../lib/api/types";
import { copyId, type Copy } from "../../lib/copy";
import type { Row } from "../mailbox";

export type DigestItem = GetAnswers["/api/digest"]["items"][number];
export type Upcoming = GetAnswers["/api/upcoming"]["items"][number];
export type Brief = GetAnswers["/api/briefing"];

export const BRIEF_CAP = 12;      // cards before "Show all"
export const COMMIT_CAP = 60;     // to-dos at a glance; the rest load as you clear

export function greeting(hour: number): string {
  return hour < 5 ? "Working late" : hour < 12 ? "Good morning" : hour < 18 ? "Good afternoon" : "Good evening";
}

export const needsYou = (items: DigestItem[]) =>
  items.filter((it) => it.needs_you).sort((a, b) => (b.importance || 0) - (a.importance || 0) || b.key - a.key);

export const safeToFile = (items: DigestItem[]) => items.filter((it) => it.safe_to_file);

export const commitments = (items: DigestItem[]) =>
  items.filter((it) => it.commitment_worthy).sort((a, b) => (b.importance || 0) - (a.importance || 0));

export const needsNote = (n: number) => (n ? `${n} need${n === 1 ? "s" : ""} you` : "Nothing needs you");

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

// [big, small]: "3:00 pm" over "today", "Sat" over "8:10 am", "Thu 6" over "all day" a week out.
export function eventWhen(e: Pick<Upcoming, "start" | "all_day">, now: Date): [string, string] {
  const d = new Date(e.start), today = new Date(now);
  today.setHours(0, 0, 0, 0);
  const days = Math.round((new Date(d).setHours(0, 0, 0, 0) - today.getTime()) / 864e5);
  const time = e.all_day ? "all day" : d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" }).toLowerCase();
  if (days <= 0) return e.all_day ? ["Today", "all day"] : [time, "today"];
  return [d.toLocaleDateString(undefined, days < 7 ? { weekday: "short" } : { weekday: "short", day: "numeric" }), time];
}

export function waitAge(ts: number, now: Date): string {
  const days = Math.floor((now.getTime() / 1000 - (ts || 0)) / 86400);
  return days < 1 ? "today" : days === 1 ? "1 day" : `${days} days`;
}

// The name in an address as a header writes it, else the address.
export function nameIn(addr: string | null | undefined): string {
  const a = String(addr ?? ""), name = a.replace(/<[^>]*>/, "").replace(/"/g, "").trim();
  return name || a.replace(/[<>]/g, "").trim();
}

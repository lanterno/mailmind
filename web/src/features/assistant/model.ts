// What the brief works out from the digest and the clock, with no DOM. The digest's rows say what the AI's verdict asks
// of you; this only sorts and words them.
import type { GetAnswers } from "../../lib/api/types";
import type { DigestItem } from "../mailbox";

export type Upcoming = GetAnswers["/api/upcoming"]["items"][number];
export type Brief = GetAnswers["/api/briefing"];

export const BRIEF_CAP = 12;      // cards before "Show all"
export const COMMIT_CAP = 60;     // to-dos at a glance; the rest load as you clear

export function greeting(hour: number): string {
  return hour < 5 ? "Working late" : hour < 12 ? "Good morning" : hour < 18 ? "Good afternoon" : "Good evening";
}

export const commitments = (items: DigestItem[]) =>
  items.filter((it) => it.commitment_worthy).sort((a, b) => (b.importance || 0) - (a.importance || 0));

export const needsNote = (n: number) => (n ? `${n} need${n === 1 ? "s" : ""} you` : "Nothing needs you");

// [big, small]: "3:00 pm" over "today", "Sat" over "8:10 am", "Thu 6" over "all day" a week out.
export function eventWhen(e: Pick<Upcoming, "start" | "all_day">, now: Date): [string, string] {
  const d = new Date(e.start), today = new Date(now);
  today.setHours(0, 0, 0, 0);
  const days = Math.round((new Date(d).setHours(0, 0, 0, 0) - today.getTime()) / 864e5);
  const time = e.all_day ? "all day" : d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" }).toLowerCase();
  if (days <= 0) return e.all_day ? ["Today", "all day"] : [time, "today"];
  return [d.toLocaleDateString(undefined, days < 7 ? { weekday: "short" } : { weekday: "short", day: "numeric" }), time];
}

// A week's meeting or deadline, whichever side of now it falls on.
export function meetingWhen(e: Pick<Upcoming, "start" | "all_day">): string {
  const d = new Date(e.start);
  const day = d.toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" });
  return e.all_day ? `${day}, all day` : `${day}, ${d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" }).toLowerCase()}`;
}

// What a weekly review looked back on, beside its title: the last seven days when it was written today, and else the seven days
// up to the day it was written, as one written ahead on Monday morning is read all week.
export function reviewRange(generated: string, now: Date): string {
  const written = new Date(generated);
  if (Number.isNaN(written.getTime()) || written.toDateString() === now.toDateString()) return "last 7 days";
  return `the 7 days to ${written.toLocaleDateString(undefined, { weekday: "long" })}`;
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

// What the brief works out from the digest and the clock, with no DOM. The digest's rows say what the AI's verdict asks
// of you; this only sorts and words them.
import type { GetAnswers } from "../../lib/api/types";
import { sameCopy, type Copy } from "../../lib/copy";
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

// While the AI reads mail that waits for its summary (AI's status says `summarizing`, as after an account's first sync),
// what needs you isn't known yet, so nothing says that nothing does.
export const READING = "Reading your new mail…";
export const summarizing = (ai: { summarizing?: boolean | null } | undefined) => ai?.summarizing === true;

export const needsNote = (n: number, reading = false) =>
  (n ? `${n} need${n === 1 ? "s" : ""} you` : reading ? READING : "Nothing needs you");

// A Your Week list's count: how many it shows, and of how many the week holds where it shows fewer ("8 of 13"), so
// its heading never says less than its tile does.
export const ofWhole = (shown: number, whole: number | null | undefined) => (whole != null && whole > shown ? `${shown} of ${whole}` : `${shown}`);

// "3:00 pm": a time on this browser's clock, lowered.
export const clock = (d: Date) => d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" }).toLowerCase();

// [big, small]: "3:00 pm" over "today", "Sat" over "8:10 am", "Thu 6" over "all day" a week out. The server lists what
// hasn't ended, so a meeting under way is said as one: "now" over "until 12:30 am" (or the day it ends, a day or more
// on), never as a start "today" that was yesterday's. One begun before today that `end` doesn't show running says the
// day it began, "yesterday", and a day's event begun earlier and running today is today's. As the Mac app's
// `EventWhen.short` says them.
export function eventWhen(e: Pick<Upcoming, "start" | "all_day"> & { end?: string }, now: Date): [string, string] {
  const d = new Date(e.start), end = e.end ? new Date(e.end) : null;
  const midnight = (t: Date) => new Date(t).setHours(0, 0, 0, 0);
  const days = Math.round((midnight(d) - midnight(now)) / 864e5);   // calendar days here: one of 23 or 25 hours is one
  const time = e.all_day ? "all day" : clock(d);
  if (!e.all_day && d <= now && end && end > now) {
    const until = end.getTime() - now.getTime() < 864e5 ? clock(end) : end.toLocaleDateString(undefined, { weekday: "short" });
    return ["now", `until ${until}`];
  }
  if (days === 0 || (days < 0 && e.all_day)) return e.all_day ? ["Today", "all day"] : [time, "today"];
  if (days === -1) return [time, "yesterday"];
  return [d.toLocaleDateString(undefined, days >= 7 || days < 0 ? { weekday: "short", day: "numeric" } : { weekday: "short" }), time];
}

// "Thu, Nov 26": a day on this browser's calendar.
const dayWords = (d: Date) => d.toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" });

// A week's meeting or deadline, whichever side of now it falls on.
export function meetingWhen(e: Pick<Upcoming, "start" | "all_day">): string {
  const d = new Date(e.start);
  return e.all_day ? `${dayWords(d)}, all day` : `${dayWords(d)}, ${clock(d)}`;
}

// The moment a time names: an instant where it names its zone, a time on this clock where it names none (as the server
// writes an event's), and a bare date's first moment here, which `new Date` alone would put at midnight in UTC.
function moment(text: string): Date | null {
  const day = /^(\d{4})-(\d{2})-(\d{2})$/.exec(text);
  const d = day ? new Date(Number(day[1]), Number(day[2]) - 1, Number(day[3])) : new Date(text);
  return text && !Number.isNaN(d.getTime()) ? d : null;
}

// When an event runs, as an invite's card says it, on the clock Today & next reads: "Sat, Nov 21, 11:55 pm – 12:40 am",
// "Thu, Nov 26, all day", "Thu, Nov 26 – Sat, Nov 28". Its end's day is said where it lasts a day or more. Null where
// the start can't be read. As the Mac app's `EventWhen.span` says it.
export function eventSpan(start: string, end: string, allDay: boolean): string | null {
  const from = moment(start);
  if (!from) return null;
  const ends = moment(end), to = ends && ends > from ? ends : null;
  if (allDay) {
    const last = to && new Date(to);   // a day's event ends as the day after its last begins
    last?.setDate(last.getDate() - 1);
    return last && last > from && last.toDateString() !== from.toDateString() ? `${dayWords(from)} – ${dayWords(last)}` : `${dayWords(from)}, all day`;
  }
  const begins = `${dayWords(from)}, ${clock(from)}`;
  if (!to) return begins;
  return to.getTime() - from.getTime() < 864e5 ? `${begins} – ${clock(to)}` : `${begins} – ${dayWords(to)}, ${clock(to)}`;
}

// What an invite's card reads of it: the event as the invite says it, and the server's own words for it.
export interface InviteTimes { start_iso: string; end_iso: string; all_day: boolean; when: string; rrule: string; rdates?: string[] | null }

export const repeats = (invite: Pick<InviteTimes, "rrule" | "rdates">) => Boolean(invite.rrule || invite.rdates?.length);

// A series' next time, where Today & next lists this message's: the server works out when a series falls next, past
// what other messages moved or cancelled, and lists the soonest few. A single event's is its own.
export function nextOf(invite: Pick<InviteTimes, "rrule" | "rdates">, copy: Copy, upcoming: readonly Upcoming[]): Upcoming | null {
  return repeats(invite) ? upcoming.find((e) => sameCopy(e, copy)) ?? null : null;
}

// When an invite's event is, on the clock the rest of the page reads, and a series' next time where Today & next has
// it, rather than the series' first: the server's words only where the times can't be read. As the Mac app's
// `InviteCard.when` says it.
export function inviteWhen(invite: InviteTimes, next: Pick<Upcoming, "start" | "end" | "all_day"> | null): string {
  return (next && eventSpan(next.start, next.end, next.all_day)) || eventSpan(invite.start_iso, invite.end_iso, invite.all_day) || invite.when;
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

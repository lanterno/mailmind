import { afterEach, describe, expect, it, vi } from "vitest";
import type { DigestItem } from "../mailbox";
import { clock, commitments, eventSpan, eventWhen, inviteWhen, meetingWhen, greeting, nameIn, needsNote, nextOf, ofWhole, READING, reviewRange, summarizing, waitAge, type Upcoming } from "./model";

const item = (key: number, over: Partial<DigestItem> = {}) => ({ key, folder: "INBOX", uid: key, importance: 1, needs_you: false, safe_to_file: false, commitment_worthy: false, ...over }) as DigestItem;

describe("greeting", () => {
  it.each([[3, "Working late"], [4, "Working late"], [5, "Good morning"], [11, "Good morning"], [12, "Good afternoon"], [17, "Good afternoon"], [18, "Good evening"], [23, "Good evening"]])(
    "at %i says %s", (hour, words) => expect(greeting(hour)).toBe(words));
});

describe("what the digest asks of you", () => {
  it("sorts commitments by importance", () => {
    const got = commitments([item(1, { commitment_worthy: true, importance: 2 }), item(2, { commitment_worthy: true, importance: 4 }), item(3)]);
    expect(got.map((x) => x.key)).toEqual([2, 1]);
  });
  it("words the count", () => {
    expect(needsNote(0)).toBe("Nothing needs you");
    expect(needsNote(1)).toBe("1 needs you");
    expect(needsNote(2)).toBe("2 need you");
  });
  it("never says nothing needs you while the AI reads mail waiting for its summary", () => {
    expect(READING).toBe("Reading your new mail…");
    expect(needsNote(0, true)).toBe(READING);
    expect(needsNote(2, true)).toBe("2 need you");
    expect(summarizing({ summarizing: true })).toBe(true);
    for (const ai of [{ summarizing: false }, { summarizing: null }, {}, undefined]) expect(summarizing(ai)).toBe(false);
  });
});

describe("the side column's words", () => {
  const now = new Date(2026, 10, 2, 12, 0);
  it("says when a week's meeting or deadline is", () => {
    expect(meetingWhen({ start: "2026-11-03T14:05", all_day: false })).toMatch(/Nov.*2:05/);
    expect(meetingWhen({ start: "2026-11-03T00:00", all_day: true })).toMatch(/Nov.*all day$/);
  });
  it("says when an event is", () => {
    expect(eventWhen({ start: new Date(2026, 10, 2, 15, 0).toISOString(), all_day: false }, now)[1]).toBe("today");
    expect(eventWhen({ start: new Date(2026, 10, 2).toISOString(), all_day: true }, now)).toEqual(["Today", "all day"]);
    expect(eventWhen({ start: new Date(2026, 10, 4).toISOString(), all_day: true }, now)[1]).toBe("all day");
  });
  it("says the next day's event by its weekday, a week out with its date", () => {
    const tomorrow = new Date(2026, 10, 3, 9, 0), later = new Date(2026, 10, 10, 9, 0);
    expect(eventWhen({ start: tomorrow.toISOString(), all_day: false }, now)).toEqual([tomorrow.toLocaleDateString(undefined, { weekday: "short" }), clock(tomorrow)]);
    expect(eventWhen({ start: later.toISOString(), all_day: false }, now)[0]).toBe(later.toLocaleDateString(undefined, { weekday: "short", day: "numeric" }));
  });
  it("says a review written today looked back over the last seven days", () => {
    expect(reviewRange(new Date(2026, 10, 2, 0, 5).toISOString(), now)).toBe("last 7 days");
  });
  it("says one written on an earlier day, as one written ahead on Monday is read all week, looked back to that day", () => {
    const monday = new Date(2026, 10, 2, 7, 0);
    const weekday = monday.toLocaleDateString(undefined, { weekday: "long" });
    expect(reviewRange(monday.toISOString(), new Date(2026, 10, 8, 23, 0))).toBe(`the 7 days to ${weekday}`);
  });
  it("says the last seven days for a time that won't read", () => {
    expect(reviewRange("", now)).toBe("last 7 days");
  });
  it("counts days waited", () => {
    const at = now.getTime() / 1000;
    expect(waitAge(at - 3600, now)).toBe("today");
    expect(waitAge(at - 86400, now)).toBe("1 day");
    expect(waitAge(at - 3 * 86400, now)).toBe("3 days");
  });
  it("reads a name out of an address", () => {
    expect(nameIn('"Lin Park" <lin@example.test>')).toBe("Lin Park");
    expect(nameIn("<lin@example.test>")).toBe("lin@example.test");
    expect(nameIn(null)).toBe("");
  });
});

// Today & next across midnight, in zones on each side of the world. The server sends an event's start and end on the
// person's wall clock with no zone ("2026-11-20T23:30"), which a browser reads as its own, so each case sets the zone
// this process reads them in, as the Mac app's `DeskWordsTests` sets its calendar's.
describe("when an event is, across midnight", () => {
  afterEach(() => vi.unstubAllEnvs());
  const events: [string, string][] = [["2026-11-20T23:30", "2026-11-21T00:30"], ["2026-11-20T23:55", "2026-11-21T00:40"], ["2026-11-21T23:00", "2026-11-21T23:30"]];
  const at = (day: number, hour: number, minute: number) => new Date(2026, 10, day, hour, minute);
  const said = (now: Date) => events.map(([start, end]) => eventWhen({ start, end, all_day: false }, now).join(" / "));
  const clockAt = (iso: string) => clock(new Date(iso));

  it.each([["Pacific/Auckland", -780], ["Europe/Paris", -60], ["America/Los_Angeles", 480], ["Asia/Kolkata", -330]] as const)("says a meeting under way is on until it ends, in %s", (zone, offset) => {
    vi.stubEnv("TZ", zone);
    expect(at(21, 0, 1).getTimezoneOffset()).toBe(offset);   // the zone took
    // Saturday at 00:01: Friday night's meetings are on, never "today" by when they began.
    expect(said(at(21, 0, 1))).toEqual([`now / until ${clockAt("2026-11-21T00:30")}`, `now / until ${clockAt("2026-11-21T00:40")}`, `${clockAt("2026-11-21T23:00")} / today`]);
    // Friday at 23:50: the first is on, the second today's, the third tomorrow's.
    const saturday = new Date("2026-11-21T23:00").toLocaleDateString(undefined, { weekday: "short" });
    expect(said(at(20, 23, 50))).toEqual([`now / until ${clockAt("2026-11-21T00:30")}`, `${clockAt("2026-11-20T23:55")} / today`, `${saturday} / ${clockAt("2026-11-21T23:00")}`]);
    // Friday at 23:00: none has begun.
    expect(said(at(20, 23, 0))).toEqual([`${clockAt("2026-11-20T23:30")} / today`, `${clockAt("2026-11-20T23:55")} / today`, `${saturday} / ${clockAt("2026-11-21T23:00")}`]);
    // Ended a moment ago, as a list a minute old can still hold it: said as yesterday's, not today's.
    expect(said(at(21, 0, 45))[0]).toBe(`${clockAt("2026-11-20T23:30")} / yesterday`);
    // A start with no end, begun before today: the day it began.
    expect(eventWhen({ start: "2026-11-20T23:30", all_day: false }, at(21, 0, 1))[1]).toBe("yesterday");
    // Under way into another day: the day it ends.
    const monday = new Date("2026-11-23T17:00").toLocaleDateString(undefined, { weekday: "short" });
    expect(eventWhen({ start: "2026-11-20T09:00", end: "2026-11-23T17:00", all_day: false }, at(21, 0, 1))).toEqual(["now", `until ${monday}`]);
    // A day's event that began yesterday and runs today is today's.
    expect(eventWhen({ start: "2026-11-20T00:00", end: "2026-11-22T00:00", all_day: true }, at(21, 0, 1))).toEqual(["Today", "all day"]);
  });

  it("says a meeting under way on a 24-hour clock and a 12-hour one alike", () => {
    vi.stubEnv("TZ", "Europe/Paris");
    const [big, small] = eventWhen({ start: "2026-11-20T23:30", end: "2026-11-21T00:30", all_day: false }, at(21, 0, 1));
    expect(big).toBe("now");
    expect(small).toMatch(/^until (00:30|12:30\s?am)$/);
  });
});

// An invite's card, as the Mac app's `testARecurringInvitesCardSaysItsNextTimeOnTheAppsClock` holds its own.
describe("when an invite's card says its event is", () => {
  afterEach(() => vi.unstubAllEnvs());
  const day = (iso: string) => new Date(iso).toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" });
  const at = (iso: string) => clock(new Date(iso));
  const copy = { key: 7, folder: "INBOX", uid: 3 };
  // A weekly invite whose series began on Friday 13 November, in the server's words for that first date.
  const invite = {
    start_iso: "2026-11-13T23:55:00+01:00", end_iso: "2026-11-14T00:40:00+01:00", all_day: false, rrule: "FREQ=WEEKLY;BYDAY=FR", rdates: [],
    when: "Fri, Nov 13 · 11:55 PM → Sat, Nov 14 12:40 AM",
  };
  const event = (key: number, start: string, end: string): Upcoming => ({
    account: "ada@example.test", all_day: false, end, folder: "INBOX", invite: true, key, location: "Kiln yard", organizer: "Otto Brand",
    recurrence: "Every week on Fri", start, summary: "Night kiln firing", thread_id: null, uid: 3,
  });
  const next = event(7, "2026-11-20T23:55", "2026-11-21T00:40"), another = event(8, "2026-11-20T09:00", "2026-11-20T10:00");

  it("says a series' next time, the one Today & next lists for its message, on the clock Today & next reads", () => {
    vi.stubEnv("TZ", "Europe/Paris");
    expect(nextOf(invite, copy, [another])).toBeNull();
    expect(nextOf(invite, copy, [another, next])).toBe(next);
    const said = inviteWhen(invite, next);
    expect(said).toBe(`${day("2026-11-20T23:55")}, ${at("2026-11-20T23:55")} – ${at("2026-11-21T00:40")}`);
    expect(said).not.toContain("13");
    expect(said).toContain(eventWhen(next, new Date(2026, 10, 20, 12, 0))[0]);
  });

  it("says a series' first time on that clock where Today & next doesn't list it", () => {
    vi.stubEnv("TZ", "Europe/Paris");
    expect(inviteWhen(invite, nextOf(invite, copy, [another]))).toBe(`${day("2026-11-13T23:55")}, ${at("2026-11-13T23:55")} – ${at("2026-11-14T00:40")}`);
  });

  it("says a single event's own time, though Today & next lists it", () => {
    vi.stubEnv("TZ", "America/Los_Angeles");
    const single = { ...invite, rrule: "", rdates: null };
    expect(nextOf(single, copy, [next])).toBeNull();
    // 23:55 in Paris is 14:55 in Los Angeles, the same day.
    expect(inviteWhen(single, null)).toBe(`${day("2026-11-13T14:55")}, ${at("2026-11-13T14:55")} – ${at("2026-11-13T15:40")}`);
  });

  it("says a day's event as its day, and one of several days from its first to its last", () => {
    vi.stubEnv("TZ", "Pacific/Auckland");
    expect(eventSpan("2026-11-26", "2026-11-27", true)).toBe(`${day("2026-11-26T00:00")}, all day`);
    expect(eventSpan("2026-11-26T00:00:00", "2026-11-27T00:00:00", true)).toBe(`${day("2026-11-26T00:00")}, all day`);
    expect(eventSpan("2026-11-26", "2026-11-29", true)).toBe(`${day("2026-11-26T00:00")} – ${day("2026-11-28T00:00")}`);
    expect(eventSpan("2026-11-26", "", true)).toBe(`${day("2026-11-26T00:00")}, all day`);
  });

  it("says the day it ends of one that lasts a day or more, and only its start where its end can't be read", () => {
    vi.stubEnv("TZ", "Asia/Kolkata");
    expect(eventSpan("2026-11-20T09:00", "2026-11-23T17:00", false)).toBe(`${day("2026-11-20T09:00")}, ${at("2026-11-20T09:00")} – ${day("2026-11-23T17:00")}, ${at("2026-11-23T17:00")}`);
    expect(eventSpan("2026-11-20T09:00", "soon", false)).toBe(`${day("2026-11-20T09:00")}, ${at("2026-11-20T09:00")}`);
    expect(eventSpan("2026-11-20T09:00", "2026-11-20T08:00", false)).toBe(`${day("2026-11-20T09:00")}, ${at("2026-11-20T09:00")}`);
  });

  it("says the server's words only where no time can be read", () => {
    expect(inviteWhen({ ...invite, rrule: "", start_iso: "" }, null)).toBe(invite.when);
    expect(inviteWhen({ ...invite, start_iso: "not a time" }, null)).toBe(invite.when);
  });
});

describe("your week's lists", () => {
  it("say how many they show of the week's whole, as its tiles count it", () => {
    expect(ofWhole(8, 13)).toBe("8 of 13");
    expect(ofWhole(3, 3)).toBe("3");
    expect(ofWhole(2, null)).toBe("2");
    expect(ofWhole(2, undefined)).toBe("2");
    expect(ofWhole(5, 4)).toBe("5");
  });
});

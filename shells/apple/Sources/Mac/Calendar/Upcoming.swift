#if os(macOS)
import SwiftUI

/// When an event is, in the few words a list has room for, as the page's `eventWhen` and `meetingWhen` say it. The
/// core sends an event's start on the person's own wall clock, "2026-11-20T11:00" with no zone (Today & next, Your
/// Week, the meeting reminder), which the page's `new Date(start)` reads as local; this only formats it here.
enum EventWhen {
    /// The moment a start names: an instant where it names its zone, a wall-clock time here where it names none (as
    /// the core writes an event's), else a day's first moment here.
    static func moment(_ start: String, calendar: Calendar = .current) -> Date? {
        let instant = ISO8601DateFormatter()
        if let date = instant.date(from: start) { return date }
        instant.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if let date = instant.date(from: start) { return date }
        if let date = wallClock(start, in: calendar.timeZone) { return date }
        return SuggestedTime.day(String(start.prefix(10)), in: calendar.timeZone)
    }

    /// A time with no zone, "2026-11-20T11:00" or with its seconds, as it reads on a clock in `zone`.
    static func wallClock(_ text: String, in zone: TimeZone) -> Date? {
        let reader = DateFormatter()
        reader.locale = Locale(identifier: "en_US_POSIX")
        reader.calendar = Calendar(identifier: .gregorian)
        reader.timeZone = zone
        reader.isLenient = false
        for format in ["yyyy-MM-dd'T'HH:mm:ss", "yyyy-MM-dd'T'HH:mm"] {
            reader.dateFormat = format
            if let date = reader.date(from: text) { return date }
        }
        return nil
    }

    /// [big, small]: "3:00 pm" over "today", "Sat" over "8:10 am", "Thu 6" over "all day" a week out. The core lists
    /// what hasn't ended, so a meeting under way is said as one: "now" over "until 00:30" (or the day it ends, where
    /// that isn't today), never as a start "today" that was yesterday's; one begun before today that `end` doesn't
    /// show running says the day it began, "yesterday".
    static func short(start: String, end: String? = nil, allDay: Bool, now: Date, calendar: Calendar = .current,
                      locale: Locale = .current) -> (big: String, small: String) {
        guard let date = moment(start, calendar: calendar) else { return (start, "") }
        let today = calendar.startOfDay(for: now)
        let days = calendar.dateComponents([.day], from: today, to: calendar.startOfDay(for: date)).day ?? 0
        let time = allDay ? String(localized: "all day") : clock(date, calendar: calendar, locale: locale)
        if !allDay, date <= now, let end = end.flatMap({ moment($0, calendar: calendar) }), end > now {
            return (String(localized: "now"), String(localized: "until \(until(end, now: now, calendar: calendar, locale: locale))"))
        }
        if days == 0 || (days < 0 && allDay) {
            return allDay ? (String(localized: "Today"), time) : (time, String(localized: "today"))
        }
        if days == -1 { return (time, String(localized: "yesterday")) }
        var style = Date.FormatStyle(locale: locale, calendar: calendar, timeZone: calendar.timeZone).weekday(.abbreviated)
        if days >= 7 || days < 0 { style = style.day() }
        return (date.formatted(style), time)
    }

    /// When a meeting under way ends: its time where that is within a day, else the day ("Sun").
    private static func until(_ end: Date, now: Date, calendar: Calendar, locale: Locale) -> String {
        if end.timeIntervalSince(now) < 24 * 3600 { return clock(end, calendar: calendar, locale: locale) }
        return end.formatted(Date.FormatStyle(locale: locale, calendar: calendar, timeZone: calendar.timeZone)
            .weekday(.abbreviated))
    }

    /// A week's meeting or deadline, whichever side of now it falls on: "Thu, Nov 20, 3:00 pm".
    static func long(start: String, allDay: Bool, calendar: Calendar = .current, locale: Locale = .current) -> String {
        guard let date = moment(start, calendar: calendar) else { return start }
        let day = date.formatted(Date.FormatStyle(locale: locale, calendar: calendar, timeZone: calendar.timeZone)
            .weekday(.abbreviated).day().month(.abbreviated))
        return allDay ? String(localized: "\(day), all day") : "\(day), \(clock(date, calendar: calendar, locale: locale))"
    }

    /// "3:00 pm", lowered as the page writes it.
    static func clock(_ date: Date, calendar: Calendar = .current, locale: Locale = .current) -> String {
        date.formatted(Date.FormatStyle(locale: locale, calendar: calendar, timeZone: calendar.timeZone)
            .hour(.defaultDigits(amPM: .abbreviated)).minute()).lowercased()
    }
}

/// Today and next: the events in your mail's invites from now on, each beside when it is, as the brief's side shows
/// them (`/api/upcoming`). A recurring series shows each time it falls.
struct UpcomingList: View {
    let events: [API.UpcomingEvent]
    let now: Date
    let open: (MailCopy) -> Void
    /// The window's answers to invites: where it has them, an invite asking for one offers Accept and Open.
    var replies: InviteReplies?
    @Environment(\.meadow) private var meadow

    /// Each event as the mockup's `.ev`: when in a column of its own, the day or the hour over the other, then its
    /// title, which opens the invite's message, and where and who under it; under an invite, its answer (`.acts`).
    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            ForEach(Array(events.enumerated()), id: \.offset) { _, event in
                VStack(alignment: .leading, spacing: 8) {
                    row(event)
                    if event.invite, let replies { answers(event, replies) }
                }
            }
        }
    }

    private func row(_ event: API.UpcomingEvent) -> some View {
        let when = EventWhen.short(start: event.start, end: event.end, allDay: event.allDay, now: now)
        return HStack(alignment: .top, spacing: 12) {
            VStack(alignment: .leading, spacing: 0) {
                Text(when.big)
                    .font(.meadow(.ui, 13, .heavy))
                    .monospacedDigit()
                    .foregroundStyle(meadow.ink)
                    .lineLimit(1)
                // A meeting under way says until when, which a twelve-hour clock writes wider than the column.
                Text(when.small)
                    .font(.meadow(.ui, 12, .semibold))
                    .foregroundStyle(meadow.ink3)
                    .lineLimit(2)
            }
            .frame(width: 62, alignment: .leading)
            VStack(alignment: .leading, spacing: 2) {
                Button(Self.title(event)) {
                    open(.named(key: event.key, folder: event.folder, uid: event.uid, account: event.account))
                }
                .deskTitle()
                .font(.meadow(.ui, 13.5, .bold))
                .foregroundStyle(meadow.ink)
                .lineLimit(2)
                .multilineTextAlignment(.leading)
                if let line = Self.line(event) {
                    Text(line)
                        .font(.meadow(.ui, 12.5))
                        .foregroundStyle(meadow.ink3)
                        .lineLimit(2)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        // One element, read as its parts show: when, which event (the title, a button's label that combining its
        // children drops), then where and who. Pressing it opens the invite, as its title does.
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(Self.spoken(event, when: when))
        .accessibilityAddTraits(.isButton)
        .accessibilityAction {
            open(.named(key: event.key, folder: event.folder, uid: event.uid, account: event.account))
        }
    }

    /// Accept, and Open for the other answers and the invite itself, under the title's column. The brief can't tell
    /// an invite already answered, but the same answer asked again finds the reply the outbox holds for it rather
    /// than sending another. Each names the event it answers, as VoiceOver reads a button alone.
    private func answers(_ event: API.UpcomingEvent, _ replies: InviteReplies) -> some View {
        let copy = MailCopy.named(key: event.key, folder: event.folder, uid: event.uid, account: event.account)
        return HStack(spacing: 6) {
            InviteAnswers(copy: copy, replies: replies, brief: true, naming: Self.title(event))
            Button("Open") { open(copy) }
                .meadowButton(small: true)
                .help("Open the invite to answer Maybe or Decline, or add it to your calendar")
                .accessibilityLabel(String(localized: "Open \(Self.title(event))"))
        }
        .padding(.leading, 62 + 12)
    }

    static func title(_ event: API.UpcomingEvent) -> String {
        event.summary.isEmpty ? String(localized: "(no title)") : event.summary
    }

    /// What VoiceOver says of an event's row: "23:30 today, Night kiln firing, Kiln yard · Otto Brand · invite".
    static func spoken(_ event: API.UpcomingEvent, when: (big: String, small: String)) -> String {
        let at = [when.big, when.small].filter { !$0.isEmpty }.joined(separator: " ")
        return [at, title(event), line(event) ?? ""].filter { !$0.isEmpty }.joined(separator: ", ")
    }

    /// Where, who asked, the series it repeats in, and whether it's an invite.
    static func line(_ event: API.UpcomingEvent) -> String? {
        var parts = [event.location, event.organizer, event.recurrence].filter { !$0.isEmpty }
        if event.invite { parts.append(String(localized: "invite")) }
        return parts.isEmpty ? nil : parts.joined(separator: " · ")
    }
}
#endif

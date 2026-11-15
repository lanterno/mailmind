#if os(macOS)
import SwiftUI

/// When an event is, in the few words a list has room for, as the page's `eventWhen` and `meetingWhen` say it. The
/// server sends each start as an instant, or a day for one that lasts all day; this only formats it here.
enum EventWhen {
    /// The moment a start names: an instant, or a day's first moment here.
    static func moment(_ start: String, calendar: Calendar = .current) -> Date? {
        let instant = ISO8601DateFormatter()
        if let date = instant.date(from: start) { return date }
        instant.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if let date = instant.date(from: start) { return date }
        return SuggestedTime.day(String(start.prefix(10)), in: calendar.timeZone)
    }

    /// [big, small]: "3:00 pm" over "today", "Sat" over "8:10 am", "Thu 6" over "all day" a week out.
    static func short(start: String, allDay: Bool, now: Date, calendar: Calendar = .current,
                      locale: Locale = .current) -> (big: String, small: String) {
        guard let date = moment(start, calendar: calendar) else { return (start, "") }
        let days = calendar.dateComponents([.day], from: calendar.startOfDay(for: now),
                                           to: calendar.startOfDay(for: date)).day ?? 0
        let time = allDay ? String(localized: "all day") : clock(date, calendar: calendar, locale: locale)
        if days <= 0 {
            return allDay ? (String(localized: "Today"), time) : (time, String(localized: "today"))
        }
        var style = Date.FormatStyle(locale: locale, calendar: calendar, timeZone: calendar.timeZone).weekday(.abbreviated)
        if days >= 7 { style = style.day() }
        return (date.formatted(style), time)
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
    @Environment(\.meadow) private var meadow

    /// Each event as the mockup's `.ev`: when in a column of its own, the day or the hour over the other, then its
    /// title, which opens the invite's message, and where and who under it.
    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            ForEach(Array(events.enumerated()), id: \.offset) { _, event in
                let when = EventWhen.short(start: event.start, allDay: event.allDay, now: now)
                HStack(alignment: .top, spacing: 12) {
                    VStack(alignment: .leading, spacing: 0) {
                        Text(when.big)
                            .font(.meadow(.ui, 13, .heavy))
                            .monospacedDigit()
                            .foregroundStyle(meadow.ink)
                        Text(when.small)
                            .font(.meadow(.ui, 12, .semibold))
                            .foregroundStyle(meadow.ink3)
                    }
                    .lineLimit(1)
                    .frame(width: 62, alignment: .leading)
                    VStack(alignment: .leading, spacing: 2) {
                        Button(event.summary.isEmpty ? String(localized: "(no title)") : event.summary) {
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
                .accessibilityElement(children: .combine)
            }
        }
    }

    /// Where, who asked, the series it repeats in, and whether it's an invite.
    static func line(_ event: API.UpcomingEvent) -> String? {
        var parts = [event.location, event.organizer, event.recurrence].filter { !$0.isEmpty }
        if event.invite { parts.append(String(localized: "invite")) }
        return parts.isEmpty ? nil : parts.joined(separator: " · ")
    }
}
#endif

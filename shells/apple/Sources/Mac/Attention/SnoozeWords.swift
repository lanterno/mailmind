#if os(macOS)
import Foundation

/// What a snooze says once the server has taken it: until when, as a send's time is said ("tomorrow 8:00",
/// "Tue, Nov 24 9:00"), and which message when it isn't the one on screen, a row's clock or the brief's card having
/// snoozed it. Every one starts "Snoozed", which gives the toast its clock.
enum SnoozeWords {
    /// The most of a subject the words carry, so the toast stays one line.
    static let subjectLength = 40

    static func said(until epoch: Int, count: Int, subject: String?, now: Date = AppSeams.clock.now,
                     calendar: Calendar = .current) -> String {
        let when = SendTimes.words(epoch, now: now, calendar: calendar)
        if count > 1 { return String(localized: "Snoozed \(count) until \(when)") }
        guard let subject = subject?.trimmingCharacters(in: .whitespacesAndNewlines), !subject.isEmpty else {
            return String(localized: "Snoozed until \(when)")
        }
        let short = subject.count > subjectLength
            ? subject.prefix(subjectLength - 1).trimmingCharacters(in: .whitespaces) + "…" : subject
        return String(localized: "Snoozed “\(short)” until \(when)")
    }
}
#endif

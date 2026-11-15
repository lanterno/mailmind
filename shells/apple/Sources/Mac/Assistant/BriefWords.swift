#if os(macOS)
import Foundation

/// What the brief works out from the digest and the clock, with no view, as the page's assistant model does. The
/// digest's rows say what the AI's verdict asks of you; this only sorts and words them.
enum BriefWords {
    /// Cards before "Show All", and to-dos at a glance, as the page shows them.
    static let briefCap = 12
    static let commitCap = 60

    static func greeting(hour: Int) -> String {
        switch hour {
        case ..<5: String(localized: "Working late")
        case ..<12: String(localized: "Good morning")
        case ..<18: String(localized: "Good afternoon")
        default: String(localized: "Good evening")
        }
    }

    /// What needs you, the most important first, then the newest.
    static func needsYou(_ items: [API.DigestItem]) -> [API.DigestItem] {
        items.filter(\.needsYou).sorted {
            let (a, b) = ($0.importance ?? 0, $1.importance ?? 0)
            return a != b ? a > b : $0.key > $1.key
        }
    }

    /// What the AI rates as safe to file: no next step, nothing asked of you.
    static func safeToFile(_ items: [API.DigestItem]) -> [API.DigestItem] {
        items.filter(\.safeToFile)
    }

    /// The AI's to-dos pulled from your mail, the most important first.
    static func commitments(_ items: [API.DigestItem]) -> [API.DigestItem] {
        items.filter(\.commitmentWorthy).sorted { ($0.importance ?? 0) > ($1.importance ?? 0) }
    }

    /// A topic's mail: the digest's rows its copies name, in its order. It can name mail the digest no longer holds.
    static func topicItems(_ copies: [API.Copy], _ items: [API.DigestItem]) -> [API.DigestItem] {
        let held = Dictionary(items.map { (Copied(key: $0.key, folder: $0.folder, uid: $0.uid), $0) },
                              uniquingKeysWith: { first, _ in first })
        return copies.compactMap { held[Copied(key: $0.key, folder: $0.folder, uid: $0.uid)] }
    }

    private struct Copied: Hashable {
        let key: Int
        let folder: String
        let uid: Int
    }

    static func needsNote(_ count: Int) -> String {
        count == 0 ? String(localized: "Nothing needs you") : count == 1 ? String(localized: "1 needs you")
            : String(localized: "\(count) need you")
    }

    /// How long you've waited on a reply: "today", "1 day", "4 days".
    static func waitAge(sent ts: Int?, now: Date) -> String {
        let days = Int(((now.timeIntervalSince1970 - Double(ts ?? 0)) / 86400).rounded(.down))
        return days < 1 ? String(localized: "today") : countNoun(days, "day", "days")
    }

    /// The name in an address as a header writes it, else the address.
    static func nameIn(_ address: String?) -> String {
        let text = address ?? ""
        let name = text.replacingOccurrences(of: #"<[^>]*>"#, with: "", options: .regularExpression)
            .replacingOccurrences(of: "\"", with: "")
            .trimmingCharacters(in: .whitespaces)
        if !name.isEmpty { return name }
        return text.replacingOccurrences(of: "<", with: "").replacingOccurrences(of: ">", with: "")
            .trimmingCharacters(in: .whitespaces)
    }

    /// What a weekly review looked back on: the last seven days when it was written today, else the seven days up to
    /// the day it was written, as one written ahead on Monday morning is read all week.
    static func reviewRange(generated: String, now: Date, calendar: Calendar = .current) -> String {
        guard let written = EventWhen.moment(generated, calendar: calendar),
              !calendar.isDate(written, inSameDayAs: now) else { return String(localized: "last 7 days") }
        let day = written.formatted(Date.FormatStyle(calendar: calendar, timeZone: calendar.timeZone).weekday(.wide))
        return String(localized: "the 7 days to \(day)")
    }

    /// A task's day for a list, "Mon, Nov 9", said to be overdue or today against the local day (`yyyy-MM-dd`).
    static func due(_ due: String?, today: String) -> (words: String, late: Bool) {
        guard let due, !due.isEmpty, let day = SuggestedTime.day(String(due.prefix(10)), in: TimeZone(identifier: "UTC")!)
        else { return ("", false) }
        let words = day.formatted(Date.FormatStyle(timeZone: TimeZone(identifier: "UTC")!)
            .weekday(.abbreviated).month(.abbreviated).day())
        if due < today { return (String(localized: "\(words) · overdue"), true) }
        return (due == today ? String(localized: "\(words) · today") : words, false)
    }

    /// Today here, as a task's due day is written.
    static func localDay(_ now: Date = Date(), zone: TimeZone = .current) -> String {
        SuggestedTime.sent(now, allDay: true, in: zone)
    }
}
#endif

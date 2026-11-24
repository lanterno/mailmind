#if os(macOS)
import Foundation

/// What the brief works out from the digest and the clock, with no view, as the page's assistant model does. The
/// digest's rows say what the AI's verdict asks of you; this only sorts and words them.
enum BriefWords {
    /// Cards before "Show All", and to-dos at a glance, as the page shows them.
    static let briefCap = 12
    static let commitCap = 60

    /// The brief's greeting, by the hour and the light the window is in, as the mockup greets each light: Dusk's
    /// light turns a late afternoon into the evening, and Night's a late evening into working late.
    static func greeting(hour: Int, light: MeadowLight = .day) -> String {
        switch hour {
        case ..<5: String(localized: "Working late")
        case ..<12: String(localized: "Good morning")
        case ..<16: String(localized: "Good afternoon")
        case ..<18: light == .day ? String(localized: "Good afternoon") : String(localized: "Good evening")
        case ..<21: String(localized: "Good evening")
        default: light == .night ? String(localized: "Working late") : String(localized: "Good evening")
        }
    }

    /// The greeting with the person's first name, when the Mac knows one: "Good evening, Ada."
    static func greeting(hour: Int, light: MeadowLight, name: String?) -> String {
        let hello = greeting(hour: hour, light: light)
        guard let name, !name.isEmpty else { return hello + "." }
        return "\(hello), \(name)."
    }

    /// The first word of a full name, as a greeting calls a person; none for a name that is only spaces.
    static func firstName(_ full: String) -> String? {
        full.split(whereSeparator: \.isWhitespace).first.map(String.init)
    }

    /// The line over the greeting: how the mail stands, from counts the window already has. What needs you, once the
    /// digest is read (`needs`), then every account's Inbox unread, once each account's counts are read (`unread`):
    /// "2 need you · 5 unread", and "Nothing unread" with neither. Nil while neither is known, so the line says the
    /// date (`dateLine`) rather than a count that is still coming. Nothing here needs the AI. While the AI reads mail
    /// that waits for its summary (`reading`), none needing you isn't known yet, so the line says it is reading.
    static func update(unread: Int?, needs: Int?, reading: Bool = false, locale: Locale = .current) -> String? {
        var parts: [String] = []
        if let needs, needs > 0 || reading { parts.append(needsNote(needs, reading: reading)) }
        if let unread, unread > 0 {
            let count = unread.formatted(.number.locale(locale))
            parts.append(String(localized: "\(count) unread"))
        }
        guard parts.isEmpty else { return parts.joined(separator: " · ") }
        return unread == nil ? nil : String(localized: "Nothing unread")
    }

    /// The weekday, then the day and the month, as this Mac writes them: the line over the greeting until the mail's
    /// counts are known.
    static func dateLine(_ now: Date, calendar: Calendar = .current, locale: Locale = .current) -> String {
        let style = Date.FormatStyle(locale: locale, calendar: calendar, timeZone: calendar.timeZone)
        return now.formatted(style.weekday(.wide)) + " · " + now.formatted(style.day().month(.wide))
    }

    /// When the brief was written, for the line beside "Your brief", and whether the mail has changed since. None while
    /// the AI is `reading` the new mail and nothing is written yet: the brief says it is reading, not that it was
    /// written.
    static func written(_ brief: API.Brief?, reading: Bool = false, calendar: Calendar = .current,
                        locale: Locale = .current) -> String? {
        guard let brief, !(reading && brief.narrative.isEmpty), let when = EventWhen.moment(brief.generated, calendar: calendar) else { return nil }
        let at = EventWhen.clock(when, calendar: calendar, locale: locale)
        return brief.stale ? String(localized: "written at \(at) · mail has changed since")
            : String(localized: "written at \(at)")
    }

    // MARK: The brief's text

    /// A run of the brief's text, and the message it names, if it names one.
    struct Run: Equatable {
        var text: String
        var names: API.DigestItem?
    }

    /// The brief's text with each message it names as a link to it, as the mockup's brief has them: where the text
    /// names a message's subject or its sender (in full, or by a first name only one sender has), the words from there
    /// to the end of the clause, at most `words` of them. One link a message, where it is first named, the most
    /// important message first.
    static func runs(_ text: String, _ items: [API.DigestItem], words: Int = 8) -> [Run] {
        var taken: [(range: Range<String.Index>, item: API.DigestItem)] = []
        let firsts = Dictionary(grouping: Set(items.compactMap(senderName)).compactMap(firstName),
                                by: { $0.lowercased() })
        for item in items.sorted(by: { ($0.importance ?? 0) > ($1.importance ?? 0) }) {
            var names: [String] = []
            if item.subject.count >= 6 { names.append(item.subject) }
            if let name = senderName(item) {
                names.append(name)
                if let first = firstName(name), first != name, first.count >= 3,
                   firsts[first.lowercased()]?.count == 1 {
                    names.append(first)
                }
            }
            for name in names {
                guard let found = wordRange(of: name, in: text, avoiding: taken.map(\.range)) else { continue }
                let range = found.lowerBound..<max(found.upperBound, clauseEnd(in: text, from: found.lowerBound,
                                                                                words: words))
                guard !taken.contains(where: { $0.range.overlaps(range) }) else { continue }
                taken.append((range, item))
                break
            }
        }
        var runs: [Run] = []
        var at = text.startIndex
        for link in taken.sorted(by: { $0.range.lowerBound < $1.range.lowerBound }) {
            if at < link.range.lowerBound { runs.append(Run(text: String(text[at..<link.range.lowerBound]))) }
            runs.append(Run(text: String(text[link.range]), names: link.item))
            at = link.range.upperBound
        }
        if at < text.endIndex { runs.append(Run(text: String(text[at...]))) }
        return runs
    }

    /// A sender's name as a sentence would say it; an address is never one.
    private static func senderName(_ item: API.DigestItem) -> String? {
        let name = (item.fromName ?? "").trimmingCharacters(in: .whitespaces)
        return name.isEmpty || name.contains("@") ? nil : name
    }

    /// Where `name` stands in `text` as whole words, in any case, outside the ranges already linked.
    private static func wordRange(of name: String, in text: String, avoiding: [Range<String.Index>])
        -> Range<String.Index>?
    {
        var from = text.startIndex
        while let found = text.range(of: name, options: [.caseInsensitive, .diacriticInsensitive],
                                     range: from..<text.endIndex) {
            let before = found.lowerBound == text.startIndex ? nil : text[text.index(before: found.lowerBound)]
            let after = found.upperBound == text.endIndex ? nil : text[found.upperBound]
            let whole = !(before.map { $0.isLetter || $0.isNumber } ?? false)
                && !(after.map { $0.isLetter || $0.isNumber } ?? false)
            if whole, !avoiding.contains(where: { $0.overlaps(found) }) { return found }
            from = found.upperBound
        }
        return nil
    }

    /// Where the clause that starts at `start` ends: before a stop, a comma, a dash, or an "and" or a "but" that
    /// joins another, after at most `words` words.
    private static func clauseEnd(in text: String, from start: String.Index, words: Int) -> String.Index {
        let stops: Set<Character> = [".", ",", ";", ":", "!", "?", "—", "–", "(", ")", "\n"]
        var at = start
        var count = 0
        var inWord = false
        var wordEnd = start
        while at < text.endIndex {
            let character = text[at]
            if stops.contains(character) { break }
            if character.isWhitespace {
                if inWord {
                    inWord = false
                    wordEnd = at
                    let rest = text[at...].drop(while: \.isWhitespace).lowercased()
                    if count >= words || rest.hasPrefix("and ") || rest.hasPrefix("but ") { return wordEnd }
                }
            } else if !inWord {
                inWord = true
                count += 1
            }
            at = text.index(after: at)
        }
        return inWord ? at : wordEnd
    }

    // MARK: The tallies

    /// One of the pills under the brief: a count and its words, or words alone, and where it goes.
    struct Tally: Equatable {
        enum Goes: Equatable {
            case needs, waiting, replyLater, worthARead, canWait, commitments, week
        }

        var count: Int?
        var words: String
        var goes: Goes
        /// In sunlight: what needs you.
        var sun = false
    }

    /// The pills under the brief, as the page's and the mockup's: what needs you in sunlight, then what waits (the
    /// Waiting place it opens: who you're waiting on, and who waits on you), what you'll reply to later, what is worth
    /// a read and what can wait, your commitments, and your week, which the AI writes, so not while AI is turned off
    /// (`week`). A count of none isn't shown.
    static func tallies(needs: Int, waiting: Int, replyLater: Int, worthARead: Int, canWait: Int,
                        commitments: Int, week: Bool = true) -> [Tally] {
        var all: [Tally] = []
        if needs > 0 {
            all.append(Tally(count: needs, words: needs == 1 ? String(localized: "needs you")
                : String(localized: "need you"), goes: .needs, sun: true))
        }
        if waiting > 0 {
            all.append(Tally(count: waiting, words: String(localized: "waiting"), goes: .waiting))
        }
        if replyLater > 0 {
            all.append(Tally(count: replyLater, words: String(localized: "to reply later"), goes: .replyLater))
        }
        if worthARead > 0 {
            all.append(Tally(count: worthARead, words: String(localized: "worth a read"), goes: .worthARead))
        }
        if canWait > 0 { all.append(Tally(count: canWait, words: String(localized: "can wait"), goes: .canWait)) }
        if commitments > 0 {
            all.append(Tally(count: commitments, words: commitments == 1 ? String(localized: "commitment")
                : String(localized: "commitments"), goes: .commitments))
        }
        if week { all.append(Tally(words: String(localized: "Your week"), goes: .week)) }
        return all
    }

    /// What is worth a read: unread mail the AI kept that asks nothing of you and isn't safe to file, the most
    /// important first, then the newest.
    static func worthARead(_ items: [API.DigestItem]) -> [API.DigestItem] {
        items.filter { !$0.needsYou && !$0.safeToFile && !$0.seen }.sorted {
            let (a, b) = ($0.importance ?? 0, $1.importance ?? 0)
            return a != b ? a > b : ($0.ts ?? 0, $0.key) > ($1.ts ?? 0, $1.key)
        }
    }

    /// What needs you, the most important first, then the newest: a conversation once, by its weightiest message, so
    /// the brief lists and counts a conversation as the Inbox shows it, as the page's `needsYou` does.
    static func needsYou(_ items: [API.DigestItem]) -> [API.DigestItem] {
        var listed = Set<[String]>()
        return everyNeed(items).filter { listed.insert(conversation(of: $0)).inserted }
    }

    /// Each message that needs you, the most important first, then the newest, a conversation's each on its own.
    static func everyNeed(_ items: [API.DigestItem]) -> [API.DigestItem] {
        items.filter(\.needsYou).sorted {
            let (a, b) = ($0.importance ?? 0, $1.importance ?? 0)
            return a != b ? a > b : $0.key > $1.key
        }
    }

    /// The conversation a message is in, in its account: the message alone where it is in none.
    static func conversation(of item: API.DigestItem) -> [String] {
        [item.account, item.threadId ?? "message \(item.key)"]
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

    /// How many need you; none, while the AI reads mail that waits for its summary (`reading`), isn't known yet, as
    /// the page's `needsNote` says.
    static func needsNote(_ count: Int, reading: Bool = false) -> String {
        count == 0 ? (reading ? AIWords.reading : String(localized: "Nothing needs you"))
            : count == 1 ? String(localized: "1 needs you") : String(localized: "\(count) need you")
    }

    /// The link under who you're waiting on, once there are more than its card shows: it opens Waiting, which holds
    /// what others wait on you for too, so it says how many the Waiting it opens holds (`held`: one account's beside
    /// that account's Inbox, as the tally beside it says), not how many of every account's wait on others (`listed`),
    /// which a count not read yet (nil) says instead, as the page does.
    static func seeAllWaiting(listed: Int, held: Int?) -> String? {
        listed > 3 ? String(localized: "See all \(held ?? listed)") : nil
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

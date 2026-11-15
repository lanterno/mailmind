#if os(macOS)
import Foundation

// What a row of the list shows and how tall that makes it (#44), as the mockup's `.row` and `rowHTML()` lay it out
// with the page's real fields (features/mailbox/MessageList.tsx): who, when, the subject, then the AI's summary in
// two lines or the snippet in one, or a receipt's amount, and its chips. No drawing here: the table asks these
// for each row's height and the cell for where each line stands.

/// How much each row shows, from the `density` preference (the page's `body[data-density]`): comfortable, cozy, or
/// compact with one line of summary and no chips.
struct RowMeasure: Equatable {
    let padTop: CGFloat
    let padBottom: CGFloat
    /// The summary's line height, in its font's size (`--row-lh`).
    let gistLeading: CGFloat
    let gistLines: Int
    let chips: Bool

    init(_ density: API.Density) {
        switch density {
        case .comfortable: (padTop, padBottom, gistLeading, gistLines, chips) = (10, 11, 1.45, 2, true)
        case .cozy: (padTop, padBottom, gistLeading, gistLines, chips) = (7, 8, 1.35, 2, true)
        case .compact: (padTop, padBottom, gistLeading, gistLines, chips) = (4, 5, 1.3, 1, false)
        }
    }

    /// Who and when, and the subject: 14 points on the page's 1.45.
    static let line: CGFloat = 14 * 1.45
    /// The snippet's one line: 13 points on 1.45.
    static let previewLine: CGFloat = 13 * 1.45
    static let gistSize: CGFloat = 13.5
    /// A chip's height, and the room above the chips.
    static let chip: CGFloat = 22
    static let chipsGap: CGFloat = 7
    static let avatar: CGFloat = 34
    /// A day's head over its first row (`.grp`): 14 above its 11-point words and 6 under.
    static let head: CGFloat = 31
    /// The list's room above its first row and under its last (`.list`'s padding), and at each side.
    static let listTop: CGFloat = 4
    static let listBottom: CGFloat = 24
    static let listSide: CGFloat = 6
    /// The row's padding at each side, and where its words start: the avatar and the gap after it.
    static let side: CGFloat = 10
    static let textX: CGFloat = side + avatar + 11

    var gistLine: CGFloat { Self.gistSize * gistLeading }

    /// The tops of each part of a row of this shape, from the row's own top.
    struct Stops: Equatable {
        let who: CGFloat
        let subject: CGFloat
        /// The summary's, the snippet's or the amount's.
        let third: CGFloat
        let chips: CGFloat?
        let height: CGFloat
    }

    func stops(_ shape: RowShape) -> Stops {
        let top = shape.head ? Self.head : 0
        let who = top + padTop
        let subject = who + Self.line + 2
        let third = subject + Self.line + (shape.gist ? 4 : 3)
        var end = third + (shape.gist ? gistLine * CGFloat(gistLines) : Self.previewLine)
        var chips: CGFloat?
        if shape.chips, self.chips {
            chips = end + Self.chipsGap
            end += Self.chipsGap + Self.chip
        }
        let height = max(end + padBottom, who + Self.avatar + padBottom)
        return Stops(who: who, subject: subject, third: third, chips: chips, height: height.rounded())
    }

    func height(_ shape: RowShape) -> CGFloat {
        stops(shape).height
    }
}

/// What decides a row's height: a day's head over it, the summary's lines or the snippet's one, and chips.
struct RowShape: Hashable {
    var head = false
    var gist = false
    var chips = false

    /// Packed into a byte, for the table's note of every row's shape; zero is a row not read yet.
    var packed: UInt8 { 1 | (head ? 2 : 0) | (gist ? 4 : 0) | (chips ? 8 : 0) }

    init(head: Bool = false, gist: Bool = false, chips: Bool = false) {
        (self.head, self.gist, self.chips) = (head, gist, chips)
    }

    init?(packed: UInt8) {
        guard packed & 1 == 1 else { return nil }
        self.init(head: packed & 2 != 0, gist: packed & 4 != 0, chips: packed & 8 != 0)
    }

    /// A row's shape: a summary takes its lines, a receipt's amount or a snippet one, and the next step or a junk
    /// tag a row of chips.
    @MainActor
    init(_ row: API.FolderRow, head: Bool) {
        self.init(head: head, gist: RowWords.third(row).isGist, chips: !RowWords.chips(row).isEmpty)
    }

    /// A row whose page hasn't come: the most usual shape, a summary and no chips.
    static let waiting = RowShape(gist: true)
    /// Today's brief: who, the tally, and a summary.
    static let brief = RowShape(gist: true)
}

/// A row's words and marks from its fields, as the page's row says them.
@MainActor
enum RowWords {
    /// The line under the subject.
    enum Third: Equatable {
        /// The AI's summary (`.gist`), in moss.
        case gist(String)
        /// A receipt's amount, then its snippet.
        case amount(String, String)
        /// The snippet (`.preview`).
        case preview(String)

        var isGist: Bool {
            if case .gist = self { return true }
            return false
        }
    }

    static func third(_ row: API.FolderRow) -> Third {
        if isReceipt(row), let amount = Ledger.amount(subject: row.subject, snippet: row.snippet) {
            return .amount(Ledger.money(amount.currency, amount.value), row.snippet)
        }
        let summary = row.summary.trimmingCharacters(in: .whitespacesAndNewlines)
        return summary.isEmpty ? .preview(row.snippet) : .gist(summary)
    }

    /// Mail the classifier filed as a receipt, whose amount the row shows (the paper trail's).
    static func isReceipt(_ row: API.FolderRow) -> Bool {
        let bucket = (row.bucket ?? "").lowercased()
        return bucket.hasPrefix("receipt") || bucket.contains("/receipt")
    }

    /// A chip under the row (`.chip`).
    struct Chip: Equatable {
        enum Tone: Equatable {
            /// What needs you: the next step, in sunlight.
            case sun
            case plain
        }

        let text: String
        let tone: Tone
    }

    /// The row's chips: the next step the AI wrote, in sunlight, and a junk tag.
    static func chips(_ row: API.FolderRow) -> [Chip] {
        var chips: [Chip] = []
        let step = row.action.trimmingCharacters(in: .whitespaces)
        if !step.isEmpty { chips.append(Chip(text: step, tone: .sun)) }
        if let junk = row.junkTag { chips.append(Chip(text: junk, tone: .plain)) }
        return chips
    }

    /// Whether the row asks something of you (`.needs`): it has a next step, so its time is in sunlight.
    static func needs(_ row: API.FolderRow) -> Bool {
        !row.action.trimmingCharacters(in: .whitespaces).isEmpty
    }

    /// The avatar's initials: the mockup's from the name, else the page's from the address.
    static func initials(_ row: API.FolderRow) -> String {
        let named = MeadowAvatar.initials(row.fromName ?? "")
        return named.isEmpty ? Monogram.initials(name: row.fromName, address: row.fromAddr) : named
    }
}

/// The day a row came on, as the list's heads say it (`dayGroup`): Today, Yesterday, Past week, Earlier.
enum DayGroup {
    static func of(_ ts: Int, now: Date, calendar: Calendar = .current) -> String {
        let today = calendar.startOfDay(for: now)
        let day = calendar.startOfDay(for: Date(timeIntervalSince1970: TimeInterval(ts)))
        let days = calendar.dateComponents([.day], from: day, to: today).day ?? 0
        return days <= 0 ? String(localized: "Today") : days == 1 ? String(localized: "Yesterday")
            : days < 7 ? String(localized: "Past week") : String(localized: "Earlier")
    }

    /// The head over `row`, where it starts a day: only in a folder's list or every Inbox's, which come newest first
    /// (`inDaySections`), and only once the row before it is read.
    static func head(_ row: API.FolderRow, after previous: API.FolderRow??, now: Date,
                     calendar: Calendar = .current) -> String? {
        guard let ts = row.ts else { return nil }
        let here = of(ts, now: now, calendar: calendar)
        switch previous {
        case .none:
            return here
        case .some(.none):
            return nil
        case .some(.some(let before)):
            guard let was = before.ts, was >= ts else { return nil }
            return of(was, now: now, calendar: calendar) == here ? nil : here
        }
    }

    /// Whether a place's list stands in days.
    static func sections(_ place: Place?) -> Bool {
        switch place {
        case .allInboxes?, .folder?, .desk(.brief)?: true
        default: false
        }
    }
}

/// Today's brief as a row at the top of the Inbox (the mockup's `briefRowHTML()`, the page's `BriefRow`): the date,
/// how many need you, wait and can wait, and who needs you.
struct BriefLine: Equatable {
    let date: String
    let tally: String
    let gist: String
    let needs: Bool

    init(needs: [API.DigestItem], waiting: Int, canWait: Int, now: Date = Date(), locale: Locale = .current) {
        date = now.formatted(Date.FormatStyle(locale: locale).weekday(.abbreviated).day().month(.abbreviated))
        tally = String(localized: "\(needs.count) need you · \(waiting) waiting · \(canWait) can wait")
        self.needs = !needs.isEmpty
        gist = Self.who(needs)
    }

    /// Who needs you, by first name: "Ren needs you.", "Ren and Mira need you.", "Ren, Mira and 2 others need you."
    static func who(_ needs: [API.DigestItem]) -> String {
        var names: [String] = []
        for item in needs {
            let name = (item.fromName ?? "").split(separator: " ").first.map(String.init)
                ?? (item.fromAddr ?? "").split(separator: "@").first.map(String.init) ?? ""
            if !name.isEmpty, !names.contains(name) { names.append(name) }
        }
        switch names.count {
        case 0 where needs.isEmpty: return String(localized: "Nothing needs you. The rest can wait.")
        case 0: return needs.count == 1 ? String(localized: "One message needs you.")
            : String(localized: "\(needs.count) messages need you.")
        case 1: return String(localized: "\(names[0]) needs you.")
        case 2: return String(localized: "\(names[0]) and \(names[1]) need you.")
        case 3: return String(localized: "\(names[0]), \(names[1]) and \(names[2]) need you.")
        default: return String(localized: "\(names[0]), \(names[1]) and \(names.count - 2) others need you.")
        }
    }
}
#endif

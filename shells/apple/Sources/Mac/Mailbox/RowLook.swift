#if os(macOS)
import Foundation

// What a row of the list shows and how tall that makes it (#44), as the mockup's `.row` and `rowHTML()` lay it out
// with the page's real fields (features/mailbox/MessageList.tsx): who, when, the subject, then the AI's summary in
// at most two lines or the snippet in one, or a receipt's amount, and its chips. No drawing here: the table asks
// these for each row's height and the cell for where each line stands.

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

    /// The summary's lines in a row of this shape: one where it fits on one, else as many as the density shows.
    func lines(_ shape: RowShape) -> Int {
        shape.oneLine ? 1 : gistLines
    }

    /// The width the summary has in a row `width` wide: inside the list's sides, from where the words start to the
    /// row's padding at the right.
    static func room(_ width: CGFloat) -> CGFloat {
        width - 2 * listSide - textX - side
    }

    /// Whether a summary `width` wide on one line (`RowFaces.gistWidth`) fits on one line of a row `rowWidth` wide.
    /// The table and the cell both ask this, so a row's height and its drawing agree.
    static func fits(_ width: Float, in rowWidth: CGFloat) -> Bool {
        CGFloat(width) <= room(rowWidth)
    }

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
        // A row with nothing under its subject closes up, its chips straight under it, as the page's empty line
        // takes no room.
        var end = shape.bare ? subject + Self.line
            : third + (shape.gist ? gistLine * CGFloat(lines(shape)) : Self.previewLine)
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

/// What decides a row's height: a day's head over it, the summary's lines, the snippet's one or none, and chips.
struct RowShape: Hashable {
    var head = false
    var gist = false
    var chips = false
    /// The summary fits on one line of the row as wide as it is now, so the row makes room for one: `.gist` is
    /// clamped to two lines, and a row is as tall as what it holds.
    var oneLine = false
    /// Nothing under the subject: no summary, and no snippet to show in its place.
    var bare = false

    /// Packed into a byte, for the table's note of every row's shape; zero is a row not read yet.
    var packed: UInt8 {
        1 | (head ? 2 : 0) | (gist ? 4 : 0) | (chips ? 8 : 0) | (oneLine ? 16 : 0) | (bare ? 32 : 0)
    }

    init(head: Bool = false, gist: Bool = false, chips: Bool = false, oneLine: Bool = false, bare: Bool = false) {
        (self.head, self.gist, self.chips, self.oneLine, self.bare) = (head, gist, chips, gist && oneLine, !gist && bare)
    }

    init?(packed: UInt8) {
        guard packed & 1 == 1 else { return nil }
        self.init(head: packed & 2 != 0, gist: packed & 4 != 0, chips: packed & 8 != 0, oneLine: packed & 16 != 0,
                  bare: packed & 32 != 0)
    }

    /// This shape with its summary, `width` wide on one line, in a row `rowWidth` wide: on one line where it fits.
    func fitting(_ width: Float?, in rowWidth: CGFloat) -> RowShape {
        var shape = self
        shape.oneLine = gist && width.map { RowMeasure.fits($0, in: rowWidth) } ?? false
        return shape
    }

    /// A row's shape: a summary takes its lines, a receipt's amount or a snippet one, an empty snippet none, and
    /// the next step or a junk tag a row of chips.
    @MainActor
    init(_ row: API.FolderRow, head: Bool) {
        let third = RowWords.third(row)
        self.init(head: head, gist: third.isGist, chips: !RowWords.chips(row).isEmpty, bare: third.isBare)
    }

    /// A row whose page hasn't come: the most usual shape, a summary and no chips.
    static let waiting = RowShape(gist: true)
    /// Today's brief: who, the tally, and a summary.
    static let brief = RowShape(gist: true)

    /// A row's shape `rowWidth` wide, and its summary's width on one line where it has a summary: the one line of
    /// text the table sets for a row, once, as its page comes, so that a new width sets none.
    @MainActor
    static func of(_ row: API.FolderRow, head: Bool, rowWidth: CGFloat) -> (shape: RowShape, gistWidth: Float?) {
        let third = RowWords.third(row)
        let shape = RowShape(row, head: head)
        guard case .gist(let text) = third else { return (shape, nil) }
        let width = RowFaces.gistWidth(text)
        return (shape.fitting(width, in: rowWidth), width)
    }
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

        /// A snippet with nothing in it: the row shows no line under its subject.
        var isBare: Bool {
            if case .preview(let snippet) = self { return snippet.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }
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
        initials(name: row.fromName, address: row.fromAddr)
    }

    static func initials(name: String?, address: String?) -> String {
        let named = MeadowAvatar.initials(name ?? "")
        return named.isEmpty ? Monogram.initials(name: name, address: address) : named
    }

    /// Your addresses, as the reader and the composer take them (`Composing.mine`): those you send from, and your
    /// accounts'.
    @MainActor
    static func mine(_ accounts: [String], status: AppStatus = .shared) -> Set<String> {
        SendGuard.mineSet((status.digest?.sendAccounts ?? []) + accounts)
    }

    /// Whom a row names, and whose avatar stands beside it.
    struct Who: Equatable {
        let name: String
        let avatarName: String?
        let avatarAddress: String?

        /// What the avatar's colour goes by: the name, else the address, as a sender's does (`FolderRow.sender`).
        var avatarKey: String { [avatarName, avatarAddress].compactMap { $0 }.first { !$0.isEmpty } ?? "" }
    }

    /// For mail from one of your own addresses, the people it went to ("To Ada", "To Ada, Lin +2"), each by first
    /// name or an organisation whole (`Words.calling`), else by address, and the first of them in the avatar; else
    /// who sent it. Your own that went to no one else is named by who wrote it, as the reader names it
    /// (`MessageWords.otherSide`), as the page's `whoOf` does.
    static func who(fromName: String?, fromAddr: String?, sentTo: [API.Addressee], sentToMore: Int,
                    mine: Set<String>) -> Who {
        let sender = Who(name: [fromName, fromAddr].compactMap { $0 }.first { !$0.isEmpty } ?? "",
                         avatarName: fromName, avatarAddress: fromAddr)
        guard let from = fromAddr, SendGuard.isMine(from.lowercased(), mine) else { return sender }
        let others = sentTo.filter { !SendGuard.isMine($0.addr.lowercased(), mine) }
        guard let first = others.first else { return sender }
        let names = others.map { Words.calling($0.name, address: $0.addr) ?? $0.addr }.joined(separator: ", ")
        let more = sentToMore > 0 ? " +\(sentToMore)" : ""
        return Who(name: String(localized: "To \(names)\(more)"), avatarName: first.name, avatarAddress: first.addr)
    }

    static func who(_ row: API.FolderRow, mine: Set<String>) -> Who {
        who(fromName: row.fromName, fromAddr: row.fromAddr, sentTo: row.sentTo, sentToMore: row.sentToMore, mine: mine)
    }

    static func who(_ item: API.DigestItem, mine: Set<String>) -> Who {
        who(fromName: item.fromName, fromAddr: item.fromAddr, sentTo: item.sentTo, sentToMore: item.sentToMore,
            mine: mine)
    }

    /// The name a brief's card gives `item`: whom it names, or a dash where it names no one.
    static func named(_ item: API.DigestItem, mine: Set<String>) -> String {
        let name = who(item, mine: mine).name
        return name.isEmpty ? "—" : name
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
/// how many need you, wait and can wait, and who needs you. What can wait is the AI's rating, so with AI turned off
/// (`canWait` nil) the line doesn't count it. While the AI reads mail that waits for its summary (`reading`), none
/// needing you isn't known yet, so the line says it is reading rather than that nothing needs you, as the page's does,
/// once, under a tally that counts only what it knows.
struct BriefLine: Equatable {
    let date: String
    let tally: String
    let gist: String
    let needs: Bool

    init(needs: [API.DigestItem], waiting: Int, canWait: Int?, reading: Bool = false, now: Date = Date(),
         locale: Locale = .current) {
        date = now.formatted(Date.FormatStyle(locale: locale).weekday(.abbreviated).day().month(.abbreviated))
        if reading, needs.isEmpty {
            tally = canWait.map { String(localized: "\(waiting) waiting · \($0) can wait") }
                ?? String(localized: "\(waiting) waiting")
            gist = AIWords.reading
        } else {
            tally = canWait.map { String(localized: "\(needs.count) need you · \(waiting) waiting · \($0) can wait") }
                ?? String(localized: "\(needs.count) need you · \(waiting) waiting")
            gist = Self.who(needs)
        }
        self.needs = !needs.isEmpty
    }

    /// Who needs you, by first name: "Ren needs you.", "Ren and Mira need you.", "Ren, Mira and 2 others need you."
    static func who(_ needs: [API.DigestItem]) -> String {
        var names: [String] = []
        for item in needs {
            // A person by their first name, an organisation whole (`Words.calling`), else the address's first part.
            let name = Words.calling(item.fromName, address: item.fromAddr)
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

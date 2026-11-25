import Foundation

/// The mailbox's places beyond a folder and All Inboxes, as the page's routes name them (app/route.ts). Plain values,
/// so `Place` can hold them on every platform; only the Mac shows them.

/// A list that isn't a folder on the server: the page's virtual folders (`VIRTUAL_FOLDERS`), each over one account or
/// over every account. What acts on them (snooze, mute, Reply later, follow-ups) is the attention area's.
enum VirtualFolder: String, CaseIterable, Hashable, Sendable {
    case replyLater = "__replylater__"
    case waiting = "__waiting__"
    case snoozed = "__snoozed__"
    case muted = "__muted__"

    /// The page's words for it (`VIRTUAL_TITLE`).
    var title: String {
        switch self {
        case .replyLater: String(localized: "Reply later")
        case .waiting: String(localized: "Waiting")
        case .snoozed: String(localized: "Snoozed")
        case .muted: String(localized: "Muted")
        }
    }

    /// The SF Symbol for the page's icon.
    var symbol: String {
        switch self {
        case .replyLater: "bookmark"
        case .waiting: "hourglass"
        case .snoozed: "clock"
        case .muted: "bell.slash"
        }
    }

    /// What the rail's count for it is, from a folders answer or the one over every account.
    func count(muted: Int, replylater: Int, snoozed: Int, waiting: Int) -> Int {
        switch self {
        case .replyLater: replylater
        case .waiting: waiting
        case .snoozed: snoozed
        case .muted: muted
        }
    }

    /// The rail shows a place only while it holds something, but Snoozed, which a person goes to look in.
    func shown(count: Int) -> Bool {
        count > 0 || self == .snoozed
    }
}

/// The two built-in views that read better as something other than rows. The query is the same either way.
enum Lane: String, Hashable, Sendable {
    case feed
    case papertrail

    /// The view's id that is this lane (`__lane_feed__`).
    init?(view id: String) {
        switch id {
        case "__lane_feed__": self = .feed
        case "__lane_papertrail__": self = .papertrail
        default: return nil
        }
    }

    var title: String {
        switch self {
        case .feed: String(localized: "Feed")
        // As the rail names it (the server's view, which the page's rail shows), and every card beside it.
        case .papertrail: String(localized: "Paper trail")
        }
    }
}

/// A search as the page's route holds one: the query in the page's grammar, the account it was asked from ("" when
/// none was in mind), a lane, the one conversation it is held to, and the sentence `q` was translated from.
struct Search: Hashable, Sendable {
    var q: String
    var account: String = ""
    var lane: Lane?
    var thread: String?
    var asked: String?
}

/// The Inbox's tabs (`InboxTab`): what needs you, everything, and what is unread.
enum InboxTab: String, CaseIterable, Hashable, Sendable {
    case needs
    case all
    case unread

    var title: String {
        switch self {
        case .needs: String(localized: "Needs you")
        case .all: String(localized: "Everything")
        case .unread: String(localized: "Unread")
        }
    }

    /// Whether a row is in the tab. A message you just opened stays put until you move on (`inTab`).
    func keeps(_ row: API.FolderRow, opened: MailCopy?) -> Bool {
        switch self {
        case .all: true
        case .needs: row.needsYou || MailCopy(row) == opened
        case .unread: !row.seen || MailCopy(row) == opened
        }
    }
}

extension Place {
    /// The account the place is of, if it is one account's.
    var account: String? {
        switch self {
        case .folder(let account, _): account
        case .virtual(_, let account): account
        case .search(let search): search.account.isEmpty ? nil : search.account
        default: nil
        }
    }

    /// Whether the place is an Inbox, which has the tabs: All Inboxes, or a folder named INBOX.
    var isInbox: Bool {
        switch self {
        case .allInboxes: true
        case .folder(_, let folder): folder.uppercased() == "INBOX"
        default: false
        }
    }

    /// Whether its rows each say their account: everywhere but one account's own folder.
    var showsAccount: Bool {
        if case .folder = self { return false }
        return true
    }

    /// The real folder a place lists, which a move from it isn't offered.
    var folderName: String? {
        if case .folder(_, let folder) = self { return folder }
        return nil
    }

    /// The search, if the place is one.
    var search: Search? {
        if case .search(let search) = self { return search }
        return nil
    }
}

/// A label as the search grammar takes it. A name can hold a space or a quote, and the server reads it back through
/// either (`labelQuery`).
func labelQuery(_ name: String) -> String {
    var escaped = ""
    for character in name {
        if character == "\"" || character == "\\" { escaped.append("\\") }
        escaped.append(character)
    }
    return "label:\"\(escaped)\""
}

/// A view's query: one that changes with the day says so (`viewQuery`).
func viewQuery(_ view: API.ListedView, now: Date = AppSeams.clock.now) -> String {
    guard view.dynamic == "last7" || view.q == "__LAST7__" else { return view.q }
    let week = now.addingTimeInterval(-7 * 86_400)
    return "after:" + String(ISO8601DateFormatter().string(from: week).prefix(10))
}

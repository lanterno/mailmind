#if os(macOS)
import Foundation

/// What a listing says besides its rows, which the list's head and its empty state say (`ListPage` in data.ts).
struct ListFacts: Equatable, Sendable {
    /// The folder is on its first read, or failed it, or is still reading its older mail.
    var syncing = false
    var syncFailed = false
    var catchingUp = false
    /// There is more than the list has read: a search, or an inbox's tab, which "Load 200 more" reads on.
    var hasMore = false
    /// What the server counts in the list, where it counts.
    var total: Int?
    /// Of the rows read, how many need you and how many are unread: the inbox's tabs count these (App.tsx).
    var needs: Int?
    var unread: Int?
    /// How many rows were read for those counts.
    var read: Int?
}

/// One read of a list: its rows as the window's rows, whether more follow, and what it says besides.
struct Sheet: Sendable {
    var rows: [API.FolderRow]
    var hasMore: Bool
    var facts: ListFacts
}

/// How the mailbox reads each of its lists, a page of 200 at a time (`PAGE` in data.ts), from the routes the page reads
/// them with: `/api/messages` for a folder or a virtual folder, `/api/inboxes` for All Inboxes, `/api/search`.
enum Lists {
    static let pageSize = 200

    /// The row a folder lists for one message, by its UID and, where it is known, its key, as the server reads it
    /// from what is stored (`/api/message/row`), however far down its folder: nil where the folder lists none of it.
    /// The row stands for that message alone. With no UID, the newest copy its key has in the folder: a move's Undo
    /// gives the copy it brings back a new one.
    static func row(_ wire: Wire, account: String, folder: String, uid: Int?, key: Int? = nil) async -> API.FolderRow? {
        guard uid != nil || key != nil,
              var row = try? await wire.get(API.Get.MessageRow.self, [
                  "account": account, "folder": folder, "uid": uid.map(String.init), "key": key.map(String.init),
              ]).row
        else { return nil }
        row.count = nil
        row.threadCount = nil
        return row
    }

    /// A folder's page, or a virtual folder's (`account` nil reads it over every account).
    static func folder(_ wire: Wire, account: String?, folder: String, grouped: Bool, offset: Int, limit: Int)
        async throws -> Sheet
    {
        let read = try await wire.get(API.Get.Messages.self, [
            "account": account, "folder": folder, "limit": String(limit), "offset": String(offset),
            "group": grouped ? "thread" : nil,
        ])
        switch read {
        case .folderListing(let listing):
            return Sheet(rows: listing.items, hasMore: listing.hasMore,
                         facts: ListFacts(syncing: listing.syncing ?? false, syncFailed: listing.syncFailed ?? false,
                                          catchingUp: listing.catchingUp ?? false, total: listing.total))
        case .snoozedListing(let listing):
            return Sheet(rows: listing.items.map(API.FolderRow.init), hasMore: listing.hasMore,
                         facts: ListFacts(total: listing.total))
        case .mutedListing(let listing):
            return Sheet(rows: listing.items.map(API.FolderRow.init), hasMore: listing.hasMore,
                         facts: ListFacts(total: listing.total))
        case .waitingListing(let listing):
            return Sheet(rows: listing.items.map(API.FolderRow.init), hasMore: listing.hasMore,
                         facts: ListFacts(total: listing.total))
        case .replyLaterListing(let listing):
            return Sheet(rows: listing.items.map(API.FolderRow.init), hasMore: listing.hasMore,
                         facts: ListFacts(total: listing.total))
        }
    }

    /// Every account's Inbox, newest first, from what is stored.
    static func inboxes(_ wire: Wire, grouped: Bool, offset: Int, limit: Int) async throws -> Sheet {
        let read = try await wire.get(API.Get.Inboxes.self, [
            "limit": String(limit), "offset": String(offset), "group": grouped ? "thread" : nil,
        ])
        return Sheet(rows: read.items, hasMore: read.hasMore,
                     facts: ListFacts(syncing: read.syncing ?? false, total: read.total))
    }

    /// A search's page: held to its conversation, or to its account when the scope says so (`searchPage`).
    static func search(_ wire: Wire, _ search: Search, scope: API.SearchScope, offset: Int, limit: Int)
        async throws -> Sheet
    {
        let page = try await hits(wire, search, scope: scope, offset: offset, limit: limit)
        return Sheet(rows: page.hits.map(API.FolderRow.init), hasMore: page.hasMore, facts: ListFacts())
    }

    /// A search's hits as the server sends them: a lane reads their dates. A lane's leave out the Trash and Junk, as
    /// the server reads `lane`.
    static func hits(_ wire: Wire, _ search: Search, scope: API.SearchScope, offset: Int, limit: Int)
        async throws -> (hits: [API.SearchHit], hasMore: Bool)
    {
        let held: API.SearchScope = search.thread != nil ? .account : search.account.isEmpty ? .all : scope
        let read = try await wire.get(API.Get.Search.self, [
            "q": search.q, "scope": held.rawValue, "account": search.account, "thread": search.thread,
            "lane": search.lane?.rawValue, "limit": String(limit), "offset": String(offset),
        ])
        switch read {
        case .searchPage(let page): return (page.items, page.hasMore)
        case .nothingAsked(let none): return (none.items, none.hasMore)
        }
    }

    /// A list the window pages through as the server pages it, telling `report` what its first page says, with the
    /// tabs' counts over that page.
    static func paged(_ read: @escaping @Sendable (_ offset: Int, _ limit: Int) async throws -> Sheet,
                      report: @escaping @Sendable (ListFacts) async -> Void) -> MailListModel.Loader
    {
        { offset, limit in
            let sheet = try await read(offset, limit)
            if offset == 0 {
                var facts = sheet.facts
                facts.needs = sheet.rows.count { $0.needsYou }
                facts.unread = sheet.rows.count { !$0.seen }
                facts.read = sheet.rows.count
                facts.total = sheet.facts.total ?? sheet.rows.count
                await report(facts)
            }
            return MailListModel.Page(total: sheet.facts.total ?? (offset + sheet.rows.count), rows: sheet.rows)
        }
    }
}

/// A list read whole up to a reach, as the page's list reads one (200 rows, and 200 more each time a person asks), and
/// served to the window a page at a time: a search, whose pages say only whether more follow, and an inbox's Needs you
/// and Unread, which keep the rows read that are in the tab. Each read starts again from the top, so what the window
/// holds is always one reading of the list. A search keeps the row open where it stood until a person leaves it, as
/// the tabs keep it: read, it would leave an `is:unread` search while it is read, and the list's keys with it.
actor Gathered {
    typealias Read = @Sendable (_ offset: Int, _ limit: Int) async throws -> Sheet

    private let read: Read
    private let keep: @Sendable (API.FolderRow, MailCopy?) -> Bool
    private let opened: @Sendable () async -> MailCopy?
    private let report: @Sendable (ListFacts) async -> Void
    /// How the row open stands now, read on its own, for a list that keeps it where the last reading had it once the
    /// list no longer does (a search's); nil for one that doesn't.
    private let standing: (@Sendable (MailCopy) async -> API.FolderRow?)?
    /// The last reading's rows, where the row open stood.
    private var last: [API.FolderRow] = []
    /// How many rows of the list to read.
    private(set) var reach = Lists.pageSize

    init(read: @escaping Read, keep: @escaping @Sendable (API.FolderRow, MailCopy?) -> Bool = { _, _ in true },
         opened: @escaping @Sendable () async -> MailCopy? = { nil },
         standing: (@Sendable (MailCopy) async -> API.FolderRow?)? = nil,
         report: @escaping @Sendable (ListFacts) async -> Void = { _ in })
    {
        self.read = read
        self.keep = keep
        self.opened = opened
        self.standing = standing
        self.report = report
    }

    nonisolated var load: MailListModel.Loader {
        { offset, limit in try await self.page(offset, limit) }
    }

    /// Read 200 more the next time.
    func readMore() {
        reach += Lists.pageSize
    }

    func page(_ offset: Int, _ limit: Int) async throws -> MailListModel.Page {
        var rows: [API.FolderRow] = []
        var seen = Set<MailCopy>()
        var first: ListFacts?
        var hasMore = false
        var at = 0
        repeat {
            let sheet = try await read(at, Lists.pageSize)
            first = first ?? sheet.facts
            // A list that moved between two reads can hold a row twice (`eachCopyOnce`).
            for row in sheet.rows where seen.insert(MailCopy(row)).inserted {
                rows.append(row)
            }
            hasMore = sheet.hasMore && !sheet.rows.isEmpty
            at += Lists.pageSize
        } while hasMore && at < reach
        let open = await opened()
        // The row open, gone from this reading but in the last, stays where it stood, as it stands now; one its folder
        // no longer lists goes.
        if let standing, let open, !rows.contains(where: { MailCopy($0) == open }),
           let at = last.firstIndex(where: { MailCopy($0) == open }), let row = await standing(open)
        {
            rows.insert(row, at: min(at, rows.count))
        }
        last = rows
        let kept = rows.filter { keep($0, open) }
        var facts = first ?? ListFacts()
        facts.hasMore = hasMore
        facts.needs = rows.count { $0.needsYou }
        facts.unread = rows.count { !$0.seen }
        facts.read = rows.count
        // What the list holds: the rows read that are in the tab.
        facts.total = kept.count
        await report(facts)
        let shown = offset < kept.count ? Array(kept[offset..<min(kept.count, offset + limit)]) : []
        return MailListModel.Page(total: kept.count, rows: shown)
    }
}
#endif

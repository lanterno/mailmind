import Foundation
import Observation

/// Where the window is: the list the sidebar chose, or a screen that stands in the list's place.
enum Place: Hashable, Sendable {
    /// Every account's Inbox, newest first (`/api/inboxes`).
    case allInboxes
    /// One account's folder (`/api/messages`).
    case folder(account: String, folder: String)
    // The mailbox's places (Mac/Mailbox/Places.swift).
    /// Reply later, Waiting, Snoozed or Muted, of one account or of every account (`account` nil).
    case virtual(VirtualFolder, account: String?)
    /// A search's results, or a view's, a label's or a lane's, which are searches.
    case search(Search)
    /// Every attachment across the accounts: the Document Hub.
    case documents
    /// A screen of the assistant's or curation's: the brief, an answer, a Screener (`DeskPlace` says where each
    /// stands).
    case desk(DeskPlace)
}

/// A copy of a message, as the page names one: its key, the folder it is in and its uid there. A row's number is no
/// name: a move, an Undo or a renumber gives the copy a new one.
struct MailCopy: Hashable, Codable, Sendable {
    let key: Int
    let folder: String
    let uid: Int
    let account: String

    init(_ row: API.FolderRow) {
        key = row.key
        folder = row.folder
        uid = row.uid
        account = row.account
    }
}

/// The message the window shows: the reader opens it, and the list keeps its row chosen while rows move.
@MainActor
@Observable
final class Selection {
    var copy: MailCopy?
}

extension API.FolderRow {
    /// The row as a message table draws it. The server says who sent it and when; the shell only lays it out.
    var drawn: MessageRow {
        let sender = [fromName, fromAddr].compactMap { $0 }.first { !$0.isEmpty } ?? ""
        return MessageRow(key: Int64(key), sender: sender, subject: subject, snippet: snippet, date: Int64(ts ?? 0),
                          unread: !seen, flagged: flagged, attachment: hasAttach)
    }
}

/// One list of mail as the route the page lists it with answers it, a page of 200 rows at a time. It keeps only the
/// pages near the rows on screen, and asks for one when the screen comes near it, so its memory doesn't grow with the
/// folder. It decides nothing about mail: the order and the rows are the server's.
@MainActor
@Observable
final class MailListModel: MessageRows {
    /// One page as a route answered it.
    struct Page: Sendable {
        let total: Int
        let rows: [API.FolderRow]
    }

    typealias Loader = @Sendable (_ offset: Int, _ limit: Int) async throws -> Page

    /// The page the page asks for (`PAGE` in features/mailbox/data.ts).
    static let pageSize = 200
    /// The pages kept on either side of those on screen.
    static let margin = 1

    @ObservationIgnored private(set) var total = 0
    /// The tables drawing the list: one in each mail window open, as every window shows the process's one list.
    @ObservationIgnored let displays = ListDisplays()
    /// Set once, when the first page has come: whether there is mail to show, and when it came.
    private(set) var firstRowsAt: Date?
    /// What a person is told when a page couldn't be read.
    private(set) var problem: String?
    /// Counted up each time a page comes with rows or a total it didn't have: what reads where the chosen copy stands
    /// (`chosenPosition`, the pane's "3 of 120") reads it again then, as a list opened under a message already open
    /// (⌘0 from one account's Inbox) holds no row until its first page comes. The pages themselves aren't watched.
    private(set) var held = 0

    @ObservationIgnored private var pages: [Int: [API.FolderRow]] = [:]
    @ObservationIgnored private var asked: Set<Int> = []
    @ObservationIgnored private var shown: Range<Int> = 0..<0
    /// Bumped by a refresh, so a page asked for before it is dropped when it comes.
    @ObservationIgnored private var generation = 0
    /// When what is held was last asked for: the list opened, or read again.
    @ObservationIgnored private var readAt = AppSeams.clock.now
    /// What reads the list again while the rows on screen say how long ago in minutes or hours (`refreshIfAging`).
    @ObservationIgnored private var aging: Task<Void, Never>?
    @ObservationIgnored private let load: Loader
    let selection: Selection?
    /// Run once, as the first page's answer comes, read or refused: what waits on knowing which rows the list holds.
    @ObservationIgnored var onceRead: (@MainActor () -> Void)?

    init(selection: Selection? = nil, load: @escaping Loader) {
        self.selection = selection
        self.load = load
    }

    /// Ask for the first page, and keep the rows' words on how long ago their mail came true as time passes.
    func start() {
        readAt = AppSeams.clock.now
        ask(0)
        guard aging == nil else { return }
        aging = Task { [weak self] in
            while !Task.isCancelled {
                try? await AppSeams.clock.sleep(for: Self.agingTick, ticking: true)
                // The list let go of (another place opened) ends this at its next tick.
                guard let self else { return }
                self.refreshIfAging(now: AppSeams.clock.now)
            }
        }
    }

    /// How often the rows on screen are looked at for words that have grown old.
    static let agingTick: Duration = .seconds(60)

    /// The server says how long ago a row's mail came ("just now", "12m ago", "5h ago") as the list is read, so those
    /// words stand still until something reads it again. Read what is held again, in place as the event stream's
    /// changes are, once a minute while a row on screen says minutes, and every ten while one says hours: only the
    /// rows whose words changed are drawn again, and the selection, the scroll and the keyboard stay. Whether it did.
    @discardableResult
    func refreshIfAging(now: Date) -> Bool {
        guard !shown.isEmpty, firstRowsAt != nil else { return false }
        let since = now.timeIntervalSince(readAt)
        guard since >= 60 else { return false }
        let ages = shown.compactMap { copy(at: $0)?.ts }.map { now.timeIntervalSince1970 - Double($0) }
        let due = ages.contains { $0 < 3600 } || (since >= 600 && ages.contains { $0 < 86_400 })
        if due { refresh() }
        return due
    }

    func row(at position: Int) -> MessageRow? {
        copy(at: position)?.drawn
    }

    /// The server's row at `position`, when its page is held.
    func copy(at position: Int) -> API.FolderRow? {
        guard position >= 0, let page = pages[position / Self.pageSize] else { return nil }
        let index = position % Self.pageSize
        return index < page.count ? page[index] : nil
    }

    /// How many rows the list holds, as `total` says, but watched: what is said beside the list of it as a whole (the
    /// empty pane's words) is drawn again as a page brings another total, as summaries fill an Inbox's Needs you.
    var watchedTotal: Int {
        _ = held
        return total
    }

    /// Where the chosen copy is among the rows held, if it is there.
    var chosenPosition: Int? {
        _ = held
        guard let chosen = selection?.copy else { return nil }
        for (number, page) in pages {
            if let index = page.firstIndex(where: { MailCopy($0) == chosen }) { return number * Self.pageSize + index }
        }
        return nil
    }

    /// The rows on screen: keep the pages around them, let the rest go, and ask for those not held.
    func setVisible(_ rows: Range<Int>) {
        shown = rows
        let wanted = wantedPages()
        for number in pages.keys where !wanted.contains(number) {
            pages.removeValue(forKey: number)
        }
        for number in wanted where pages[number] == nil {
            ask(number)
        }
    }

    /// Change the row held for `copy` now, as the server has just changed it, so the row says so at once rather than
    /// once the list is read again. Whether a row held was changed.
    @discardableResult
    func patch(_ copy: MailCopy, _ change: (inout API.FolderRow) -> Void) -> Bool {
        for (number, page) in pages {
            guard let index = page.firstIndex(where: { MailCopy($0) == copy }) else { continue }
            var row = page[index]
            change(&row)
            guard row != page[index] else { return false }
            pages[number]?[index] = row
            displays.rowsChanged(IndexSet(integer: number * Self.pageSize + index), totalChanged: false)
            return true
        }
        return false
    }

    /// Read again what is held, as the page refetches a list in place when the event stream says mail changed.
    func refresh() {
        readAt = AppSeams.clock.now
        generation += 1
        asked.removeAll()
        let held = pages.isEmpty ? [0] : Array(pages.keys).sorted()
        for number in held {
            ask(number)
        }
    }

    private func wantedPages() -> Set<Int> {
        guard !shown.isEmpty else { return [0] }
        let first = max(0, shown.lowerBound / Self.pageSize - Self.margin)
        let last = (shown.upperBound - 1) / Self.pageSize + Self.margin
        let pageCount = max(1, (total + Self.pageSize - 1) / Self.pageSize)
        let upTo = min(last, pageCount - 1)
        return first <= upTo ? Set(first...upTo) : []
    }

    private func ask(_ number: Int) {
        guard !asked.contains(number) else { return }
        asked.insert(number)
        let (load, generation) = (load, generation)
        Task { [weak self] in
            let read: Result<Page, any Error>
            do {
                read = .success(try await load(number * Self.pageSize, Self.pageSize))
            } catch {
                read = .failure(error)
            }
            self?.arrived(read, page: number, generation: generation)
        }
    }

    private func arrived(_ read: Result<Page, any Error>, page number: Int, generation: Int) {
        guard generation == self.generation else { return }
        asked.remove(number)
        switch read {
        case .failure(let error):
            problem = (error as? Problem)?.description ?? String(localized: "This list couldn't be read.")
        case .success(let page):
            problem = nil
            let totalChanged = total != page.total
            total = page.total
            let before = pages[number] ?? []
            pages[number] = page.rows
            let first = number * Self.pageSize
            let changed = IndexSet((0..<max(before.count, page.rows.count))
                .filter { $0 >= before.count || $0 >= page.rows.count || before[$0] != page.rows[$0] }
                .map { first + $0 })
            if firstRowsAt == nil { firstRowsAt = Date() }
            if totalChanged || !changed.isEmpty {
                held += 1
                displays.rowsChanged(changed, totalChanged: totalChanged)
            }
        }
        if let once = onceRead {
            onceRead = nil
            once()
        }
    }
}


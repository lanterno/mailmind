#if os(macOS)
import Foundation
import Observation

/// Today's brief, as the page's BriefPane reads it: the digest (what needs you, what can be filed, the AI's state),
/// the brief the AI wrote of it, today and next, who you're waiting on, and what Tidy filed. A GET reads only what is
/// stored; opening the brief writes the day's one if there is none and AI is on, and never asks at the daily limit:
/// only a person's press asks to go over it. Passing over its row with the arrow keys isn't opening it: that shows
/// what is stored, and offers to write the rest.
@MainActor
@Observable
final class BriefModel {
    enum Trouble: Equatable {
        /// Today's AI limit is reached.
        case limit
        case failed(String)
    }

    private(set) var digest: API.Digest?
    /// Settings' choices, read while AI is off, so the brief's note says what AI mode would send as Settings does.
    private(set) var preferences: API.Preferences?
    private(set) var brief: API.Brief?
    private(set) var writing = false
    /// None is stored for today and none was written on opening: the brief offers to write it.
    private(set) var missing = false
    private(set) var trouble: Trouble?
    private(set) var upcoming: [API.UpcomingEvent] = []
    private(set) var waiting: [API.WaitingRow] = []
    private(set) var tidyRun: API.TidyRun?
    /// How many wait in the Waiting and Reply Later stacks the brief's tallies open (`stacksAccount`): one account's,
    /// or every account's together. Nil until read.
    private(set) var waitingCount: Int?
    private(set) var replyLaterCount: Int?
    /// The account whose stacks the tallies open, and so count: the one whose Inbox the brief stands beside, or nil
    /// for every account's, as the page counts them where its `goToFolder` opens them.
    @ObservationIgnored var stacksAccount: () -> String? = { nil }
    /// The account the stacks were last counted for, "" for every account's; nil before they were.
    @ObservationIgnored private var counted: String?
    /// Rows on their way out: a read that lands meanwhile can't bring one back.
    private(set) var filing: Set<MailCopy> = []
    /// The Tidy run a person dismissed, kept as the page keeps it in its storage.
    private(set) var dismissedTidy: String?
    /// The paper trail's receipts, newest first, for its card beside the brief: as far as one page of the search goes.
    private(set) var receipts: [API.SearchHit] = []
    /// The paper trail's query, as the server's views name it, where the window has one.
    @ObservationIgnored var paperTrailQuery: () -> String? = { nil }

    @ObservationIgnored let link: CoreLink
    @ObservationIgnored let notices: DeskNotices
    @ObservationIgnored var changed: () -> Void = {}
    @ObservationIgnored private let defaults: UserDefaults
    static let tidyDismissedKey = "mm_tidy_dismissed"

    init(link: CoreLink, notices: DeskNotices, defaults: UserDefaults = .standard) {
        self.link = link
        self.notices = notices
        self.defaults = defaults
        dismissedTidy = defaults.string(forKey: Self.tidyDismissedKey)
    }

    /// Whether AI is on, once the digest says.
    var aiOn: Bool? { digest.map { $0.ai.isOn } }

    /// AI was turned off in Settings: the brief shows only what the digest holds, with no word of the AI.
    var aiTurnedOff: Bool { digest?.ai.isTurnedOff ?? false }

    /// The AI is reading mail that waits for its summary, as the digest last said: until it has, what needs you isn't
    /// known, so the brief, its line over the Inbox and its tallies say so rather than that nothing does.
    var summarizing: Bool { digest?.ai.isSummarizing ?? false }

    /// What the brief's Needs you says with nothing in it.
    var calm: String { summarizing ? AIWords.reading : String(localized: "Nothing needs you right now.") }

    /// The digest's rows still here.
    var items: [API.DigestItem] {
        (digest?.items ?? []).filter { !filing.contains(Self.copy(of: $0)) }
    }

    var needs: [API.DigestItem] { BriefWords.needsYou(items) }

    /// The line over the greeting: how the mail stands (`BriefWords.update`), from what needs you once the digest is
    /// read and the Inbox's unread the window counts, and the date until either is known.
    func line(unread: Int?, now: Date) -> String {
        BriefWords.update(unread: unread, needs: digest == nil ? nil : needs.count, reading: summarizing)
            ?? BriefWords.dateLine(now)
    }
    /// What the AI rates as safe to file, which the brief offers to file and counts as able to wait. None while AI is
    /// turned off: the ratings are the AI's, so neither the offer nor its count shows then.
    var tidy: [API.DigestItem] { aiTurnedOff ? [] : BriefWords.safeToFile(items) }
    var worthARead: [API.DigestItem] { BriefWords.worthARead(items) }

    /// The brief's topics, each with the mail it names still here.
    var topics: [(label: String, items: [API.DigestItem])] {
        (brief?.groups ?? []).map { ($0.label, BriefWords.topicItems($0.messages, items)) }.filter { !$0.items.isEmpty }
    }

    /// What the AI failed with last, as the digest says, for "isn't available right now (…)".
    var failing: String? {
        guard let ai = digest?.ai, ai.isOn, ai.lastOk == false else { return nil }
        return ai.lastError.flatMap { $0.isEmpty ? nil : $0 }
    }

    /// Whether there is more than one account, so each card names its own.
    var multi: Bool { (digest?.accounts.count ?? 0) > 1 }

    /// Your addresses, as the digest names them: a card of your own mail names whom it went to (`RowWords.who`).
    var mine: Set<String> { SendGuard.mineSet((digest?.sendAccounts ?? []) + (digest?.accounts ?? [])) }

    /// A Tidy run that can still be taken back and wasn't dismissed.
    var tidyShown: API.TidyRun? {
        guard let run = tidyRun, let group = run.group, group != dismissedTidy else { return nil }
        return run
    }

    static func copy(of item: API.DigestItem) -> MailCopy {
        .named(key: item.key, folder: item.folder, uid: item.uid, account: item.account)
    }

    /// Whether the brief is on screen, so mail changing reads all it shows again.
    private(set) var opened = false
    /// Its tallies are kept, for the rail's count and the Inbox's brief row, so mail changing reads them again.
    @ObservationIgnored private(set) var tallied = false
    /// The brief was arrived at, not opened: its next load writes none.
    @ObservationIgnored private var glancing = false
    /// The brief on screen was opened on purpose, not only arrived at: AI turned on again writes the one AI-off left
    /// unwritten, as opening it would have.
    @ObservationIgnored private var deliberate = false
    /// The read under way after mail changed, and whether another waits behind it.
    @ObservationIgnored private var rereading: Task<Void, Never>?
    @ObservationIgnored private var rereadsAgain = false

    /// Open the brief: everything it shows, and with none written today and AI on, today's written, unless it was
    /// only arrived at (`glance`).
    func load() async {
        guard let routes = link.routes else { return }
        let writes = !glancing
        glancing = false
        deliberate = writes
        // Opened beside another account's Inbox: the stacks counted for the last one aren't this one's.
        if counted != (stacksAccount() ?? "") { (waitingCount, replyLaterCount) = (nil, nil) }
        let first = !opened
        opened = true
        await reread()?.value
        if first || brief == nil, !writing { await readBrief(routes, writes: writes) }
    }

    /// The brief is about to show without being opened (the arrow keys onto its row): the load it makes writes none.
    func glance() {
        glancing = true
    }

    /// The brief left the screen: mail changing reads only its tallies again.
    func close() {
        opened = false
        deliberate = false
    }

    /// One of AI's settings or a key changed in Settings: read how AI stands again. Only where that turned it on, off
    /// before and on now, with the brief opened on purpose and none written because AI was off as it opened, is today's
    /// asked for, as opening it with AI on asks. Another provider, model, URL or limit with AI on already asks nothing,
    /// and nor does a brief only arrived at.
    @discardableResult
    func aiChanged() -> Task<Void, Never> {
        let wasOn = aiOn
        let reading = reread()
        return Task { [weak self] in
            await reading?.value
            guard let self, wasOn == false, self.aiOn == true, self.opened, self.deliberate, self.brief == nil,
                  !self.writing, let routes = self.link.routes else { return }
            await self.readBrief(routes, writes: true)
        }
    }

    /// Keep the tallies the rail and the Inbox's brief row show: read now, and again whenever mail changes.
    @discardableResult
    func keepTallied() -> Task<Void, Never>? {
        tallied = true
        return reread()
    }

    /// Mail changed: read the tallies again, and while the brief is on screen all it shows. One read runs at a time,
    /// and at most one more waits behind it to read whatever changed meanwhile, so a burst of events costs two reads,
    /// never one each, and never two at once.
    @discardableResult
    func reread() -> Task<Void, Never>? {
        guard tallied || opened else { return nil }
        if let rereading {
            rereadsAgain = true
            return rereading
        }
        let task = Task { [weak self] in
            repeat {
                guard let self else { return }
                self.rereadsAgain = false
                if self.opened { await self.refresh() } else { await self.readTallies() }
            } while self?.rereadsAgain == true
            self?.rereading = nil
        }
        rereading = task
        return task
    }

    /// Read again what the digest and the side say, as the page does when mail changes. The brief itself stays, save
    /// one with nothing to say while the AI was reading mail that waited for its summary: once it has read it, the
    /// brief is read again, as the page's is, or "Nothing to brief yet." would stay until the brief was opened again.
    func refresh() async {
        guard let routes = link.routes else { return }
        let wasSummarizing = summarizing
        await readDigest(routes)
        if wasSummarizing, !summarizing, let shown = brief, shown.narrative.isEmpty, !writing {
            await readBrief(routes, writes: deliberate)
        }
        await readSide(routes)
        tidyRun = try? await routes.get(API.Get.Tidy.self, [:])
        await readStacks(routes)
    }

    /// Read only the digest, which the tallies come from: what needs you and what can wait.
    func readTallies() async {
        guard let routes = link.routes else { return }
        await readDigest(routes)
    }

    private func readDigest(_ routes: any RouteCaller) async {
        if let read = try? await routes.get(API.Get.Digest.self, [:]) {
            digest = read
            filing.removeAll()
            if !read.ai.isOn { preferences = (try? await routes.get(API.Get.Preferences.self, [:]))?.preferences }
        }
    }

    private func readBrief(_ routes: any RouteCaller, writes: Bool) async {
        do {
            switch try await routes.get(API.Get.Briefing.self, [:]) {
            case .brief(let stored):
                brief = stored
                missing = false
            case .briefMissing:
                brief = nil
                missing = true
                // No one pressed: it never asks at the limit.
                if writes, aiOn == true, !writing { await write(again: false, asking: nil) }
            }
        } catch {
            trouble = Self.trouble(error)
        }
    }

    /// Today and next, and who you're waiting on. Either may be missing: the brief stands without it.
    func readSide(_ routes: any RouteCaller) async {
        upcoming = (try? await routes.get(API.Get.Upcoming.self, [:]).items) ?? []
        waiting = (try? await routes.get(API.Get.WaitingOnOthers.self, [:]).items) ?? []
        await readReceipts(routes)
    }

    /// The paper trail's first page, a local search that spends nothing; none where the window has no paper trail.
    private func readReceipts(_ routes: any RouteCaller) async {
        guard let q = paperTrailQuery(), !q.isEmpty else {
            receipts = []
            return
        }
        let read = try? await routes.get(API.Get.Search.self, [
            "q": q, "scope": API.SearchScope.all.rawValue, "account": "", "limit": String(Lists.pageSize),
            "offset": "0",
        ])
        switch read {
        case .searchPage(let page)?: receipts = page.items
        case .nothingAsked(let none)?: receipts = none.items
        case nil: break
        }
    }

    private func readStacks(_ routes: any RouteCaller) async {
        let whose = stacksAccount()
        var (waiting, later) = (0, 0)
        for account in whose.map({ [$0] }) ?? digest?.accounts ?? [] {
            if let folders = try? await routes.get(API.Get.Folders.self, ["account": account]) {
                waiting += folders.waiting
                later += folders.replylater
            }
        }
        (waitingCount, replyLaterCount) = (waiting, later)
        counted = whose ?? ""
    }

    /// Write the brief, or write it again from the mail as it is now (`again`), on a person's press. Both run the AI,
    /// and at today's limit ask first.
    func write(again: Bool) async {
        await write(again: again, asking: link.asksOverLimit)
    }

    private func write(again: Bool, asking: AskOverLimit?) async {
        guard let routes = link.routes, !writing else { return }
        writing = true
        trouble = nil
        defer { writing = false }
        do {
            brief = again
                ? try await routes.post(API.Post.BriefingRefresh.self, API.NoBody(), asking: asking)
                : try await routes.post(API.Post.Briefing.self, API.NoBody(), asking: asking)
            missing = false
            if again { await readSide(routes) }
        } catch {
            trouble = Self.trouble(error)
        }
    }

    /// What went wrong, for the words beside "Try Again". AI off isn't a failure: the brief says so.
    static func trouble(_ error: any Error) -> Trouble? {
        if DeskRefusal.isOverLimit(error) { return .limit }
        if DeskRefusal.isAIOff(error) { return nil }
        return .failed(String(localized: "couldn’t reach the server"))
    }

    /// Mail a verb sent away from where it was, in any window (a list's, a reader's, a message's own, the gate's): the
    /// brief leaves it out at once, as the page's brief leaves out whatever is being filed, rather than once the server
    /// has carried the change and the digest is read again, which a server that doesn't answer puts off.
    func left(_ copies: [MailCopy]) {
        filing.formUnion(copies)
    }

    /// File one card's message to Archive or Trash, and say where it went: a card has no list behind it to see the
    /// row leave.
    func file(_ item: API.DigestItem, _ verb: API.FilingVerb) async {
        guard let routes = link.routes else { return }
        let copy = Self.copy(of: item)
        filing.insert(copy)
        let body = API.MoveBody(folder: copy.folder, key: copy.key, uid: copy.uid)
        do {
            let filed = verb == .trash
                ? try await routes.post(API.Post.Trash.self, body)
                : try await routes.post(API.Post.Archive.self, body)
            notices.say(Self.filedWords(verb, filed), undo: filed.undoable ? Mailbox.words(verb).undo : nil)
        } catch {
            filing.remove(copy)
            notices.say(ReadOnlyWords.said(error, else: verb == .trash ? String(localized: "Couldn’t delete")
                                                                     : String(localized: "Couldn’t archive")), refused: true)
        }
        changed()
    }

    /// Where a card's mail went, as the page says it from a card.
    static func filedWords(_ verb: API.FilingVerb, _ filed: API.Filed) -> String {
        let queued = filed.status == "queued"
        switch verb {
        case .trash: return queued ? String(localized: "Trashed · once the server is back")
            : String(localized: "Deleted · in the mailbox's Trash")
        case .archive: return queued ? String(localized: "Archived · once the server is back")
            : String(localized: "Archived · in the mailbox's Archive")
        case .junk: return queued ? String(localized: "Marked junk · once the server is back") : String(localized: "Marked junk")
        }
    }

    /// File every message the AI rates as safe to file to Archive, as one undoable action.
    func fileTidy() async {
        guard let routes = link.routes else { return }
        let rows = tidy
        guard !rows.isEmpty else { return }
        let copies = rows.map(Self.copy)
        filing.formUnion(copies)
        do {
            let filed = try await routes.post(API.Post.BulkFile.self, API.BulkFileBody(
                rows: copies.map { API.BulkRow(folder: $0.folder, key: $0.key, uid: $0.uid, whole: false) },
                undoGroup: newUndoGroup(), verb: .archive))
            notices.say(filed.rowsFailed > 0
                ? String(localized: "Archived \(copies.count - filed.rowsFailed), \(filed.rowsFailed) failed")
                : String(localized: "Archived \(copies.count)"),
                undo: filed.undoable ? Mailbox.words(.archive).undo : nil)
        } catch {
            filing.subtract(copies)
            notices.say(ReadOnlyWords.said(error, else: String(localized: "Couldn’t archive")), refused: true)
        }
        changed()
    }

    /// Take back everything Tidy filed in its latest run.
    func undoTidy() async {
        guard let routes = link.routes, let group = tidyRun?.group else { return }
        do {
            _ = try await routes.post(API.Post.TidyUndo.self, API.TidyUndoBody(group: group))
            tidyRun = try? await routes.get(API.Get.Tidy.self, [:])
            changed()
        } catch {
            notices.say(ReadOnlyWords.said(error, else: String(localized: "Couldn’t take that back.")), refused: true)
        }
    }

    func dismissTidy() {
        guard let group = tidyRun?.group else { return }
        defaults.set(group, forKey: Self.tidyDismissedKey)
        dismissedTidy = group
    }
}
#endif

#if os(macOS)
import Foundation
import Observation

/// Today's brief, as the page's BriefPane reads it: the digest (what needs you, what can be filed, the AI's state),
/// the brief Claude wrote of it, today and next, who you're waiting on, and what Tidy filed. A GET reads only what is
/// stored; opening the brief writes the day's one if there is none and AI is on, and never asks at the daily limit:
/// only a person's press asks to go over it.
@MainActor
@Observable
final class BriefModel {
    enum Trouble: Equatable {
        /// Today's AI limit is reached.
        case limit
        case failed(String)
    }

    private(set) var digest: API.Digest?
    private(set) var brief: API.Brief?
    private(set) var writing = false
    private(set) var trouble: Trouble?
    private(set) var upcoming: [API.UpcomingEvent] = []
    private(set) var waiting: [API.WaitingRow] = []
    private(set) var tidyRun: API.TidyRun?
    /// How many wait in the Waiting and Reply Later stacks, every account's together.
    private(set) var waitingCount = 0
    private(set) var replyLaterCount = 0
    /// Rows on their way out: a read that lands meanwhile can't bring one back.
    private(set) var filing: Set<MailCopy> = []
    /// The Tidy run a person dismissed, kept as the page keeps it in its storage.
    private(set) var dismissedTidy: String?

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
    var aiOn: Bool? { digest.map { $0.ai.engine != "none" } }

    /// The digest's rows still here.
    var items: [API.DigestItem] {
        (digest?.items ?? []).filter { !filing.contains(Self.copy(of: $0)) }
    }

    var needs: [API.DigestItem] { BriefWords.needsYou(items) }
    var tidy: [API.DigestItem] { BriefWords.safeToFile(items) }

    /// The brief's topics, each with the mail it names still here.
    var topics: [(label: String, items: [API.DigestItem])] {
        (brief?.groups ?? []).map { ($0.label, BriefWords.topicItems($0.messages, items)) }.filter { !$0.items.isEmpty }
    }

    /// What the AI failed with last, as the digest says, for "isn't available right now (…)".
    var failing: String? {
        guard let ai = digest?.ai, ai.engine != "none", ai.lastOk == false else { return nil }
        return ai.lastError.flatMap { $0.isEmpty ? nil : $0 }
    }

    /// Whether there is more than one account, so each card names its own.
    var multi: Bool { (digest?.accounts.count ?? 0) > 1 }

    /// A Tidy run that can still be taken back and wasn't dismissed.
    var tidyShown: API.TidyRun? {
        guard let run = tidyRun, let group = run.group, group != dismissedTidy else { return nil }
        return run
    }

    static func copy(of item: API.DigestItem) -> MailCopy {
        .named(key: item.key, folder: item.folder, uid: item.uid, account: item.account)
    }

    /// Whether the brief has been opened, so what changes reads it again.
    private(set) var opened = false

    /// Open the brief: everything it shows, and with none written today and AI on, today's written.
    func load() async {
        guard let routes = link.routes else { return }
        let first = !opened
        opened = true
        await refresh()
        if first || brief == nil, !writing { await readBrief(routes) }
    }

    /// Read again what the digest and the side say, as the page does when mail changes. The brief itself stays.
    func refresh() async {
        guard let routes = link.routes else { return }
        if let read = try? await routes.get(API.Get.Digest.self, [:]) {
            digest = read
            filing.removeAll()
        }
        await readSide(routes)
        tidyRun = try? await routes.get(API.Get.Tidy.self, [:])
        await readStacks(routes)
    }

    private func readBrief(_ routes: any RouteCaller) async {
        do {
            switch try await routes.get(API.Get.Briefing.self, [:]) {
            case .brief(let stored):
                brief = stored
            case .briefMissing:
                brief = nil
                if aiOn == true, !writing { await write(again: false, asking: nil) }   // no one pressed: never asks
            }
        } catch {
            trouble = Self.trouble(error)
        }
    }

    /// Today and next, and who you're waiting on. Either may be missing: the brief stands without it.
    func readSide(_ routes: any RouteCaller) async {
        upcoming = (try? await routes.get(API.Get.Upcoming.self, [:]).items) ?? []
        waiting = (try? await routes.get(API.Get.WaitingOnOthers.self, [:]).items) ?? []
    }

    private func readStacks(_ routes: any RouteCaller) async {
        var (waiting, later) = (0, 0)
        for account in digest?.accounts ?? [] {
            if let folders = try? await routes.get(API.Get.Folders.self, ["account": account]) {
                waiting += folders.waiting
                later += folders.replylater
            }
        }
        (waitingCount, replyLaterCount) = (waiting, later)
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
            notices.say(Self.filedWords(verb, filed), undoable: filed.undoable)
        } catch {
            filing.remove(copy)
            notices.say(verb == .trash ? String(localized: "Couldn’t delete") : String(localized: "Couldn’t archive"))
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
                : String(localized: "Archived \(copies.count)"), undoable: filed.undoable)
        } catch {
            filing.subtract(copies)
            notices.say(String(localized: "Couldn’t archive"))
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
            notices.say(String(localized: "Couldn’t take that back."))
        }
    }

    func dismissTidy() {
        guard let group = tidyRun?.group else { return }
        defaults.set(group, forKey: Self.tidyDismissedKey)
        dismissedTidy = group
    }
}
#endif

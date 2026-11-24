#if os(macOS)
import Foundation
import Observation

/// Looking after the mailbox itself, as the page's Curate drawer does: senders flooding you and cleaning them up
/// (block, sweep, unsubscribe), what each inbox holds and its sweep, the rules MailMind has learned, your filters, and
/// what it has done. Cleaning up and sweeping both show what they would do before they do it, and a destructive one
/// takes a second press.
@MainActor
@Observable
final class CurateModel {
    /// One sender's cleanup, previewed: how far the block goes, whether to unsubscribe too, and the second press.
    struct Cleaning: Equatable {
        var preview: API.SenderPreview
        var scope: API.BlockScope
        var unsubscribe: Bool
        var armed = false

        var method: String { preview.unsub.method }

        /// Whether the cleanup sends an unsubscribe, which can't be undone: one the box asks for, by one click or an
        /// email. A page opened in the browser sends nothing; the person unsubscribes there or not.
        var sendsUnsubscribe: Bool { unsubscribe && (method == "one-click" || method == "mailto") }

        /// What the sheet says of unsubscribing: there is no way to, one goes that Undo can't take back, the page
        /// opens in the browser, or the box is unticked and nothing goes.
        enum UnsubscribeLine: Equatable {
            case none, sends, opensPage, notAsked
        }

        var unsubscribeLine: UnsubscribeLine {
            if method == "none" { return .none }
            if sendsUnsubscribe { return .sends }
            return unsubscribe && method == "http-page" ? .opensPage : .notAsked
        }

        /// The sheet's note on what Undo takes back.
        var undoNote: String {
            Self.undoes + (sendsUnsubscribe ? " " + Self.cantUndo : "")
        }

        static let undoes = String(localized: "Block + sweep undo with Undo.")
        static let cantUndo = String(localized: "Unsubscribing can’t be undone.")
    }

    /// Several senders' cleanup, previewed, with why each is here once the AI has said.
    struct Batching: Equatable {
        var preview: API.BatchPreview
        var armed = false
        var reasons: [String: String] = [:]
        var reasoning = false
    }

    /// The filter a sheet holds: one being made, or with an id one being edited.
    struct FilterForm: Equatable, Identifiable {
        var id: String?
        var query: String
        var star: Bool
        var read: Bool

        /// A filter has to do something, so a new one stars.
        static func new(_ query: String = "") -> FilterForm {
            FilterForm(id: nil, query: query, star: true, read: false)
        }
    }

    private(set) var flood: API.NoisySenders?
    private(set) var floodFailed = false
    private(set) var stats: API.Stats?
    private(set) var rules: API.Rules?
    private(set) var audit: [API.AuditEvent] = []
    var marked: Set<String> = []
    var cleaning: Cleaning?
    /// Whether to unsubscribe, as the person last ticked it for a sender, by address: the sheet opens with it again,
    /// so a box unticked never comes back ticked over an unsubscribe Undo can't take back. Kept while MailMind runs.
    @ObservationIgnored private var unsubscribeChoices: [String: Bool] = [:]
    var batching: Batching?
    /// An account's sweep, planned and waiting for Confirm: what goes, bucket by bucket, and what stays.
    private(set) var sweepArmed: API.AccountSwept?
    /// A sweep's plan or the sweep itself, while the server reads the account's Inbox and moves what goes: its card
    /// says so, and Sweep and Confirm wait until the answer comes.
    private(set) var sweepBusy: SweepBusy?

    /// What a sweep is doing, and of which account.
    struct SweepBusy: Equatable {
        enum Step: Equatable {
            /// Reading the Inbox to plan what goes.
            case planning
            /// Moving what goes to the Trash.
            case sweeping
        }

        let account: String
        let step: Step

        /// What the account's card says meanwhile.
        var words: String {
            switch step {
            case .planning: String(localized: "Reading this Inbox to plan the sweep…")
            case .sweeping: String(localized: "Sweeping…")
            }
        }
    }
    var filterForm: FilterForm?

    @ObservationIgnored let link: CoreLink
    @ObservationIgnored let notices: DeskNotices
    @ObservationIgnored var changed: () -> Void = {}
    /// Opens a sender's unsubscribe page in the person's browser, where nothing is sent on its own.
    @ObservationIgnored var openPage: (URL) -> Void = { _ in }

    init(link: CoreLink, notices: DeskNotices) {
        self.link = link
        self.notices = notices
    }

    // MARK: Reading

    func load() async {
        guard let routes = link.routes else { return }
        do {
            flood = try await routes.get(API.Get.CleanupSenders.self, [:])
            floodFailed = false
        } catch {
            floodFailed = flood == nil
        }
        await readRules(routes)
        stats = try? await routes.get(API.Get.Stats.self, [:])
        audit = (try? await routes.get(API.Get.Audit.self, ["limit": "60"]).events) ?? audit
    }

    private func readRules(_ routes: any RouteCaller) async {
        if let read = try? await routes.get(API.Get.Rules.self, [:]) { rules = read.rules }
    }

    // MARK: One sender

    func previewCleanup(_ sender: String) async {
        guard let routes = link.routes else { return }
        do {
            let preview = try await routes.get(API.Get.CleanupSender.self, ["addr": sender])
            let method = preview.unsub.method
            let chosen = unsubscribeChoices[preview.fromAddr.lowercased()]
            cleaning = Cleaning(preview: preview, scope: .sender,
                                unsubscribe: chosen ?? (method == "one-click" || method == "mailto"))
        } catch let problem as Problem {
            notices.say(problem.detail ?? String(localized: "error"), refused: true)
        } catch {
            notices.say(String(localized: "Couldn’t load sender"), refused: true)
        }
    }

    /// The box ticked or unticked: kept for this sender, and a cleanup armed asks again, since what it does changed.
    func chooseUnsubscribe(_ on: Bool) {
        guard let asked = cleaning else { return }
        unsubscribeChoices[asked.preview.fromAddr.lowercased()] = on
        cleaning?.unsubscribe = on
        cleaning?.armed = false
    }

    /// The first press arms the cleanup; the second runs it.
    func pressCleanup() async {
        guard var asked = cleaning else { return }
        guard asked.armed else {
            asked.armed = true
            cleaning = asked
            return
        }
        await runCleanup(asked)
    }

    private func runCleanup(_ asked: Cleaning) async {
        guard let routes = link.routes else { return }
        cleaning = nil
        let sender = asked.preview.fromAddr
        notices.say(String(localized: "Cleaning up \(sender)…"))
        let done: API.CleanedUp
        do {
            done = try await routes.post(API.Post.CleanupExecute.self, API.CleanupBody(
                blockScope: asked.scope, confirmToken: asked.preview.confirmToken, doUnsubscribe: asked.unsubscribe,
                fromAddr: sender, undoGroup: newUndoGroup()))
        } catch {
            notices.say(ReadOnlyWords.said(error, else: String(localized: "Cleanup failed")), refused: true)
            return
        }
        if asked.method == "http-page", asked.unsubscribe, let page = done.browserUrl.flatMap(URL.init(string:)),
           page.scheme == "https" || page.scheme == "http" {
            openPage(page)
        }
        let extra = Self.cleaned(done.status)
        notices.say(String(localized: "Blocked · swept \(done.swept)") + (extra.isEmpty ? "" : " · \(extra)"),
                    undo: String(localized: "Clean Up"))
        await again()
    }

    /// What became of an unsubscribe, in the page's words.
    static func cleaned(_ status: String) -> String {
        switch status {
        case "unsubscribed_oneclick": String(localized: "unsubscribed")
        case "mailto_sent": String(localized: "unsub email sent")
        case "needs_browser": String(localized: "opened the unsub page")
        case "no_method": String(localized: "no unsub link")
        case "failed": String(localized: "unsub couldn’t send — retry later")
        case "no_token": String(localized: "unsub not sent — reopen to retry")
        case "read_only": String(localized: "unsub not sent — MailMind is read-only")
        default: ""
        }
    }

    // MARK: Several senders

    func previewBatch() async {
        guard let routes = link.routes else { return }
        let senders = (flood?.senders ?? []).map(\.fromAddr).filter { marked.contains($0) }
        guard !senders.isEmpty else { return }
        do {
            batching = Batching(preview: try await routes.post(API.Post.CleanupBatchPreview.self,
                                                               API.BatchSendersBody(senders: senders)))
        } catch {
            notices.say(DeskRefusal.words(error, else: String(localized: "Couldn’t load those senders")), refused: true)
            return
        }
        await explainBatch(senders)
    }

    /// Why each is worth cleaning up: an extra the preview stands without, so it makes no fuss at the limit or with
    /// AI off.
    private func explainBatch(_ senders: [String]) async {
        guard let routes = link.routes else { return }
        batching?.reasoning = true
        let reasons = try? await routes.post(API.Post.CleanupRationale.self, API.RationaleBody(senders: senders))
        batching?.reasoning = false
        batching?.reasons = reasons?.rationales ?? [:]
    }

    func pressBatch() async {
        guard var asked = batching else { return }
        guard asked.armed else {
            asked.armed = true
            batching = asked
            return
        }
        guard let routes = link.routes else { return }
        let senders = asked.preview.senders.map(\.fromAddr)
        batching = nil
        marked.removeAll()
        notices.say(String(localized: "Cleaning up \(countNoun(senders.count, "sender", "senders"))…"))
        do {
            let done = try await routes.post(API.Post.CleanupBatch.self, API.BatchCleanupBody(
                senders: senders, undoGroup: newUndoGroup()))
            let failed = done.sweepFailed > 0 ? String(localized: " · \(done.sweepFailed) failed") : ""
            notices.say(String(localized: "Blocked \(done.blocked.count) · swept \(done.swept)") + failed,
                        undo: String(localized: "Clean Up"))
        } catch {
            notices.say(ReadOnlyWords.said(error, else: String(localized: "Cleanup failed")), refused: true)
            return
        }
        await again()
    }

    // MARK: Sweeps

    /// Plan an account's sweep, and wait for Confirm. One at a time: a plan reads the whole Inbox from its server.
    /// Another account's plan waiting for Confirm goes as this one starts, so only one plan is ever on screen: its
    /// Confirm would do nothing while this one runs.
    func armSweep(_ account: String) async {
        guard let routes = link.routes, sweepBusy == nil else { return }
        sweepArmed = nil
        sweepBusy = SweepBusy(account: account, step: .planning)
        defer { sweepBusy = nil }
        do {
            let planned = try await routes.post(API.Post.Sweep.self, API.SweepBody(account: account, dryRun: true))
            guard let result = planned.results.first else { return notices.say(String(localized: "Nothing to sweep")) }
            if let error = result.error {
                notices.say(Self.sweepStopped(error), refused: true)
            } else if result.planned == 0, let held = result.heldBack, held > 0 {
                notices.say(String(localized: "Nothing to sweep: \(held) starred or unread stay"))
            } else if result.planned == 0 {
                notices.say(String(localized: "Nothing to sweep"))
            } else {
                sweepArmed = result
            }
        } catch {
            notices.say(Self.sweepFailed(error, planning: true), refused: true)
        }
    }

    func abortSweep() {
        sweepArmed = nil
        notices.say(String(localized: "Aborted"))
    }

    func sweep() async {
        guard let routes = link.routes, let armed = sweepArmed, sweepBusy == nil else { return }
        sweepArmed = nil
        sweepBusy = SweepBusy(account: armed.account, step: .sweeping)
        notices.say(String(localized: "Sweeping \(armed.planned)…"))
        do {
            let swept = try await routes.post(API.Post.Sweep.self, API.SweepBody(
                account: armed.account, dryRun: false, undoGroup: newUndoGroup()))
            if let result = swept.results.first {
                let unverified = (result.stragglers ?? []).count
                let words = result.error.map {
                    Self.sweepStopped($0, moved: result.moved ?? 0, undoable: swept.undoGroup != nil)
                } ?? String(localized: "Swept \(result.moved ?? 0)/\(result.planned)")
                    + (unverified > 0 ? String(localized: " · \(unverified) unverified") : "")
                // Whatever went, or may have, one Undo brings it back from the Trash: a sweep stopped where the server
                // couldn't be asked again keeps a way back for all of it. One that moved nothing offers none.
                let undoable = swept.undoGroup != nil && ((result.moved ?? 0) > 0 || result.error != nil)
                notices.say(words, undo: undoable ? String(localized: "Sweep") : nil)
            }
        } catch {
            notices.say(Self.sweepFailed(error, planning: false), refused: true)
        }
        sweepBusy = nil
        await again()
    }

    /// How many a card's Sweep button offers: the junk a sweep would move now, read and unstarred, or nil when none,
    /// and then there is no button.
    static func sweepOffer(_ account: API.AccountStats) -> Int? {
        account.sweepable > 0 ? account.sweepable : nil
    }

    /// What a junk bucket is called before a sweep; one this app doesn't know goes by the server's name for it.
    static func bucketWords(_ bucket: String) -> String {
        switch bucket {
        case "spam/scam": String(localized: "Spam and scams")
        case "blocked": String(localized: "Senders you blocked")
        case "newsletters/marketing": String(localized: "Newsletters and marketing")
        case "automated-notifications": String(localized: "Automatic notifications")
        default: bucket
        }
    }

    /// A bucket's senders with the most of it, and how many when more than one.
    static func sweepSenders(_ bucket: API.SweepBucket) -> String {
        bucket.senders.map { $0.count > 1 ? "\($0.fromAddr) (\($0.count))" : $0.fromAddr }.joined(separator: ", ")
    }

    /// What a sweep's plan says under what goes: what stays, what waits for the next sweep, and what Undo can't bring
    /// back.
    static func sweepNotes(_ plan: API.AccountSwept) -> [String] {
        var notes: [String] = []
        if let held = plan.heldBack, held > 0 {
            notes.append(held == 1
                ? String(localized: "1 starred or unread message stays in your Inbox.")
                : String(localized: "\(held) starred or unread messages stay in your Inbox."))
        }
        if plan.capped == true {
            notes.append(String(localized: "The newest \(plan.planned) only — sweep again for the rest."))
        }
        if let lost = plan.noUndo, lost > 0 {
            notes.append(String(localized: "\(lost) of them can’t be brought back with Undo."))
        }
        return notes
    }

    /// What Undo does for a planned sweep, said beside Confirm: puts it all back, or the rest when some of it has no
    /// Message-ID to find it by in the Trash (the page's `undoBrings`).
    static func undoLine(_ plan: API.AccountSwept) -> String {
        (plan.noUndo ?? 0) > 0 ? String(localized: "Undo puts the rest back.") : String(localized: "Undo puts it all back.")
    }

    /// A sweep the server stopped, at its plan or as it moved mail, after `moved` went: plain words with what to do
    /// next, and the server's class of the trouble after them in brackets, for whoever looks into it, never alone (the
    /// page's `sweepStopped`). `undoable` when the server kept a way back though none was counted: it couldn't be
    /// asked again how much went, so some may be in the Trash already.
    static func sweepStopped(_ error: String, moved: Int = 0, undoable: Bool = false) -> String {
        let why = switch moved {
        case ...0 where undoable: String(localized: """
            Couldn’t reach the mail server, so the sweep stopped part way and MailMind couldn’t check how much went. \
            Undo puts back whatever did; sweep again for the rest.
            """)
        case ...0: String(localized: "Couldn’t reach the mail server, so nothing was swept. Try again in a moment.")
        case 1: String(localized: """
            Couldn’t reach the mail server, so the sweep stopped after 1. Undo brings it back; sweep again for the rest.
            """)
        default: String(localized: """
            Couldn’t reach the mail server, so the sweep stopped after \(moved). Undo brings them back; sweep again for \
            the rest.
            """)
        }
        return "\(why) (\(error))"
    }

    /// A sweep, or its plan, that got no answer: the server's own words when it refused, else what to do (the page's
    /// `sweepFailed`).
    static func sweepFailed(_ error: any Error, planning: Bool) -> String {
        if ReadOnlyWords.refuses(error) { return ReadOnlyWords.refused }
        if let problem = error as? Problem, problem.isRefusal, let detail = problem.detail, !detail.isEmpty {
            return detail
        }
        return planning ? String(localized: "Couldn’t look at this Inbox. Try again in a moment.")
            : String(localized: "The sweep didn’t finish. Try again in a moment.")
    }

    // MARK: Rules and filters

    /// Keep or block a sender or a domain, or remove a rule: whether it was kept.
    @discardableResult
    func rule(_ token: String, _ verdict: API.RuleChange) async -> Bool {
        let token = token.trimmingCharacters(in: .whitespaces)
        guard let routes = link.routes, !token.isEmpty else { return false }
        do {
            let changed = try await routes.post(API.Post.Rules.self, API.RuleBody(token: token, verdict: verdict))
            rules = changed.rules
            switch verdict {
            case .keep: notices.say(String(localized: "Keeping \(token)"))
            case .block: notices.say(String(localized: "Blocked \(token)"))
            case .remove: notices.say(String(localized: "Removed \(token)"))
            }
            stats = try? await routes.get(API.Get.Stats.self, [:])
            // A rule changed how stored mail reads.
            if changed.reclassified > 0 { self.changed() }
            return true
        } catch {
            notices.say(Self.ruleFailed(error), refused: true)
            return false
        }
    }

    /// A rule that wasn't kept: the server's own words when it refused (what isn't one address or one domain, say),
    /// else that it failed.
    static func ruleFailed(_ error: any Error) -> String {
        if ReadOnlyWords.refuses(error) { return ReadOnlyWords.refused }
        if let problem = error as? Problem, problem.isRefusal, let detail = problem.detail, !detail.isEmpty {
            return detail
        }
        return String(localized: "Rule failed")
    }

    /// Save the filter the sheet holds. A refusal says, in the server's words, what a filter can't look at; it is
    /// thrown for the sheet to show.
    func save(_ form: FilterForm) async throws {
        guard let routes = link.routes else { return }
        _ = try await routes.post(API.Post.Filters.self, API.FilterChange(
            id: form.id, q: form.query, read: form.read, star: form.star, verb: form.id == nil ? .create : .update))
        notices.say(form.id == nil ? String(localized: "Filter created") : String(localized: "Filter saved"))
        filterForm = nil
        await readRules(routes)
    }

    func removeFilter(_ filter: API.MailFilter) async {
        guard let routes = link.routes else { return }
        do {
            _ = try await routes.post(API.Post.Filters.self, API.FilterChange(id: filter.id, verb: .delete))
            notices.say(String(localized: "Filter removed"))
            await readRules(routes)
        } catch {
            notices.say(String(localized: "Couldn’t remove the filter"), refused: true)
        }
    }

    /// What a filter does, in the page's words.
    static func does(_ filter: API.MailFilter) -> String {
        filter.star && filter.read ? String(localized: "Star and mark read")
            : filter.star ? String(localized: "Star") : String(localized: "Mark read")
    }

    /// Read the lists and everything here again, as the page does after a cleanup or sweep.
    private func again() async {
        changed()
        await load()
    }
}
#endif

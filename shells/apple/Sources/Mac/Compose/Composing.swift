#if os(macOS)
import AppKit
import Foundation
import Observation

/// What a send is doing once its composer has gone: the seconds with Undo, the time it went out for, or what failed.
enum SendFloat: Equatable {
    case progress(String, clock: Bool)
    case countdown(id: Int, left: Int, total: Int)
    case done(String, clock: Bool)
    /// What failed, and in small type what is kept and why.
    case failed(String, detail: String? = nil)
}

/// A reply written ahead for a message (`draftAhead.ts`): ready, or being written.
enum DraftChip: Equatable {
    case ready, drafting
}

/// The app's writing: the composers open, each in a window of its own, what they share (who you are, the
/// snippets, the people the lookup knew), a send after its composer closed (Undo Send, a failure's Edit), and the
/// replies written ahead. It speaks the routes the page's compose feature speaks.
@MainActor
@Observable
final class Composing: ComposeHost {
    static let shared = Composing()

    /// How long a message stays on screen before a reply is written ahead for it: not on a fast skim.
    static let dwellToDraft = Duration.milliseconds(1200)
    static let liveTries = 2
    static let liveWait = Duration.seconds(10)

    @ObservationIgnored private(set) var core: (any Routes)?
    /// The window's alert once the app's core is attached; a test's own answer, or none.
    @ObservationIgnored private(set) var asksOverLimit: AskOverLimit?
    private(set) var digest: API.Digest?
    private(set) var preferences: API.Preferences?
    private(set) var snippets: [API.Snippet] = []
    private(set) var composers: [UUID: Composer] = [:]
    private(set) var float: SendFloat?
    /// How long the float stays before it goes by itself: 0 until it is acted on.
    @ObservationIgnored private(set) var floatLife: Double = 0
    /// What the main window says for a moment, as the page's toasts.
    private(set) var notice: ToastWords?
    /// The replies written ahead, by the message's key.
    private(set) var chips: [Int: DraftChip] = [:]
    /// Opens a composer's window: the main window's scene gives it.
    @ObservationIgnored var openWindow: ((UUID) -> Void)?
    /// What else reads again when mail went out or a draft changed: the window's list and folders.
    @ObservationIgnored var mailChanged: (() -> Void)?

    @ObservationIgnored private var knownCache: [String: Person] = [:]
    @ObservationIgnored private var listening: Task<Void, Never>?
    @ObservationIgnored private var hearingAI: Task<Void, Never>?
    @ObservationIgnored private var undoTimer: Task<Void, Never>?
    @ObservationIgnored private var floatTimer: Task<Void, Never>?
    @ObservationIgnored private var noticeTimer: Task<Void, Never>?
    @ObservationIgnored private var failedSend: ComposeSeed?
    @ObservationIgnored private var undoing: (() async -> Void)?
    @ObservationIgnored private var snippetsAsked = 0
    @ObservationIgnored private var snippetsFrom = 0
    @ObservationIgnored private var shownCopy: MailCopy?
    @ObservationIgnored private var dwell: Task<Void, Never>?
    @ObservationIgnored private var openingAhead: Int?
    /// The last mailto: link that came before the window could write.
    @ObservationIgnored private var waitingMail: Mailto?
    /// The top bar's outbox, made with the routes and read at once, then again whenever the outbox or the changes
    /// waiting change: its button shows from launch whenever something waits.
    private(set) var outbox: OutboxTray?
    /// The sends waiting for the time a person chose (Send later), whose drafts Drafts and the reader say so of
    /// (`Scheduled`).
    private(set) var scheduled: [API.OutboxItem] = []
    /// Draws the list's rows again once what waits for later changed: a draft's row says when it goes.
    @ObservationIgnored var scheduledChanged: (() -> Void)?

    /// A reply written in the reader, under the message it answers (#44), rather than in a window of its own.
    struct InlineReply {
        let composer: Composer
        let copy: MailCopy
        /// Whether its words are the reply MailMind wrote ahead, which the box says.
        var drafted = false
    }

    /// The reply open in the reader, if one is.
    private(set) var inline: InlineReply?
    /// Replies closed in the reader's box with their words saved, by the message each answers: r on that message
    /// opens the words there again, rather than an empty box.
    @ObservationIgnored private var boxDrafts: [MailCopy: Composer] = [:]
    /// The saves closing started, by composer, until each settles: Reply waits for them, so the message it reads says
    /// what answers it once they have landed.
    @ObservationIgnored private var closingSaves: [UUID: Task<Bool?, Never>] = [:]
    /// Drafts closed while their server's copy was still to go, the server's gate too busy for the closing save, and
    /// when: the push job sends each, and says so only as the outbox's event, which no list hears. Until it has, for
    /// as long as the job may take, each such event reads the lists again, so Drafts shows it once it is there.
    @ObservationIgnored private(set) var reaching: [Int: Date] = [:]
    /// The longest the push job waits between tries (`draft_journal::BACKOFF_MOST`).
    static let reachingFor: TimeInterval = 15 * 60
    /// The message the reader offers its reply box for: a plain reply to it opens there. Moving to another message
    /// closes a reply left open, which saves it to Drafts as closing a window does.
    var inlineSlot: MailCopy? {
        didSet {
            guard inlineSlot != oldValue, let open = inline, open.copy != inlineSlot else { return }
            inline = nil
            open.composer.close()
        }
    }
    /// Bumped each time the reader offers its box, so a box drawn again keeps the slot (`withdrawInline`).
    @ObservationIgnored var slotTurn = 0

    /// Begin with the core: who you are, the preferences, and what the event stream says.
    func attach(_ core: Core) {
        guard self.core == nil else { return }
        self.core = core
        asksOverLimit = { await OverLimitQuestion.onScreen.ask($0) }
        Task {
            await readDigest()
            await readPreferences()
            await readScheduled()
        }
        readOutbox(core)
        // AI turned on or off in Settings: the composers' AI rows follow at once.
        hearingAI = Task { [weak self] in
            for await _ in NotificationCenter.default.notifications(named: .mailmindAIChanged) {
                await self?.readDigest()
            }
        }
        listening = Task { [weak self] in
            guard let events = try? await core.events() else { return }
            for await event in events {
                guard let self else { return }
                switch event.name {
                case "outbox":
                    self.outboxChanged()
                    self.heardTheOutbox()
                case "commands-changed": self.outboxChanged()
                case "sync-done", "resync": await self.readDigest()
                case "draft-ready":
                    if let key = event.decoded([String: JSONValue].self)?["key"], case .number(let n) = key {
                        self.draftReady(Int(n))
                    }
                default: break
                }
            }
        }
    }

    /// Routes of a test's own, and who the digest says you are, with no event stream.
    func use(_ routes: any Routes, digest: API.Digest?, preferences: API.Preferences? = nil,
             asking: AskOverLimit? = nil)
    {
        core = routes
        asksOverLimit = asking
        self.digest = digest
        self.preferences = preferences
        readOutbox(routes)
    }

    /// The outbox's tray over `routes`, read now.
    private func readOutbox(_ routes: any Routes) {
        let tray = OutboxTray(routes: routes, say: { [weak self] in self?.say($0) },
                              sendsChanged: { [weak self] in Task { await self?.readScheduled() } })
        outbox = tray
        Task { await tray.load() }
    }

    func readDigest() async {
        guard let core else { return }
        if let read = try? await core.get(API.Get.Digest.self, [:]) { digest = read }
        if aiTurnedOff { forgetAhead() }
        if let waiting = waitingMail, canWrite {
            waitingMail = nil
            write(waiting)
        }
    }

    /// A mailto: link macOS handed the app: a composer now, or once the window has opened the core and read who you
    /// are (a link can launch the app).
    func write(_ mail: Mailto) {
        guard canWrite else {
            waitingMail = mail
            return
        }
        open(mail.seed)
    }

    private var canWrite: Bool { core != nil && openWindow != nil && digest != nil }

    func readPreferences() async {
        guard let core else { return }
        if let read = try? await core.get(API.Get.Preferences.self, [:]) { preferences = read.preferences }
    }

    /// Change a preference as the page does, and keep what the server answers.
    func change(_ changed: API.ChangedPreferences) async {
        guard let core else { return }
        do {
            preferences = try await core.post(API.Post.Preferences.self, changed).preferences
        } catch {
            say("Couldn’t save that setting: \(said(error))")
        }
    }

    /// Say something in the main window for a moment, and to a screen reader: a refusal (`refused`) with the alert.
    func say(_ text: String, for seconds: Double = 2.6, refused: Bool = false) {
        notice = ToastWords(text: text, refused: refused)
        announce(text)
        noticeTimer?.cancel()
        noticeTimer = Task { [weak self] in
            try? await AppSeams.clock.sleep(for: .seconds(seconds))
            guard !Task.isCancelled else { return }
            self?.notice = nil
        }
    }

    // MARK: ComposeHost

    var routes: (any Routes)? { core }

    var senders: [String] {
        guard let digest else { return [] }
        return digest.sendAccounts.isEmpty ? digest.accounts : digest.sendAccounts
    }

    var mine: Set<String> {
        SendGuard.mineSet((digest?.sendAccounts ?? []) + (digest?.accounts ?? []))
    }

    var tone: API.DraftTone { preferences?.draftTone ?? .balanced }
    /// The limit isn't among the generated types yet: the server refuses a message whose files come to more.
    var attachLimit: Int? { nil }
    var aiOn: Bool { digest?.ai.isOn ?? false }
    var readOnly: Bool { digest?.profile == "read-only" }
    /// AI was turned off in Settings: a reply written ahead isn't offered either.
    var aiTurnedOff: Bool { digest?.ai.isTurnedOff ?? false }

    func signature(_ email: String) -> String {
        let own = digest?.signatures[email] ?? ""
        return own.isEmpty ? digest?.signatureDefault ?? "" : own
    }

    /// A read and a change each answer with the whole list, in any order: an answer to what was asked before the one
    /// the list last came from is let go.
    private func showSnippets(_ asked: Int, _ list: [API.Snippet]) {
        guard asked >= snippetsFrom else { return }
        snippetsFrom = asked
        snippets = list
    }

    func loadSnippets() {
        guard let core else { return }
        snippetsAsked += 1
        let asked = snippetsAsked
        Task {
            // The last list read stands if this fails: none of it stops a message being written.
            if let read = try? await core.get(API.Get.Snippets.self, [:]) { showSnippets(asked, read.snippets) }
        }
    }

    func changeSnippets(_ change: API.SnippetChange) async -> String? {
        guard let core else { return nil }
        snippetsAsked += 1
        let asked = snippetsAsked
        do {
            showSnippets(asked, try await core.post(API.Post.Snippets.self, change).snippets)
            return nil
        } catch {
            return "Couldn’t \(change.verb?.rawValue ?? "change") that snippet: \(said(error))"
        }
    }

    func known(_ address: String) -> Person? { knownCache[address] }
    func remember(_ address: String, _ person: Person) { knownCache[address] = person }
    func now() -> Int { Int(AppSeams.clock.now.timeIntervalSince1970) }
    /// Seconds on the app's clock that only moves forward (`AppSeams.clock.instant`), from when this process first
    /// asked: how long the send guard has shown.
    func clock() -> Double {
        let since = AppSeams.clock.instant - Self.started
        return Double(since.components.seconds) + Double(since.components.attoseconds) / 1e18
    }

    private static let started = AppSeams.clock.instant

    func announce(_ text: String) {
        NSAccessibility.post(element: NSApp as Any, notification: .announcementRequested,
                             userInfo: [.announcement: text, .priority: NSAccessibilityPriorityLevel.high.rawValue])
    }

    func draftsChanged() { mailChanged?() }

    /// The outbox changed: a draft closed before its copy reached its server may have reached it, so the lists are
    /// read again while one may still be on its way. Drafts as it opens catches up with a copy MailMind put there.
    func heardTheOutbox(now: Date = AppSeams.clock.now) {
        reaching = reaching.filter { now.timeIntervalSince($0.value) < Self.reachingFor }
        if !reaching.isEmpty { draftsChanged() }
    }
    func foldersChanged() { mailChanged?() }
    func outboxChanged() {
        if let outbox { Task { await outbox.load() } }
        Task { await readScheduled() }
    }

    /// Read again what waits to go at a time a person chose, and draw the mail again when that changed.
    func readScheduled() async {
        guard let core, let outbox = try? await core.get(API.Get.Outbox.self, ["limit": "200"]) else { return }
        let waiting = outbox.items.filter { $0.scheduled && $0.state == "queued" }
        guard waiting != scheduled else { return }
        scheduled = waiting
        scheduledChanged?()
    }

    /// What a draft closed while read only is on says: kept on this Mac, in the tray's words, not in Drafts.
    static let keptWords = String(localized: "Draft saved · \(OutboxTray.kept)")

    /// A draft read only keeps on this Mac, closed: where it is kept is said, and the outbox's tray is read again,
    /// so it shows there at once, as it does once MailMind opens again; no Drafts folder holds it.
    func keptOnThisMac(_ composer: Composer) {
        say(Self.keptWords)
        outboxChanged()
    }

    func closing(_ composer: Composer, save: Task<Bool?, Never>, snapshot: ComposeSeed) {
        if let open = inline, open.composer === composer { boxDrafts[open.copy] = composer }
        closingSaves[composer.id] = save
        Task {
            let saved = await save.value
            closingSaves.removeValue(forKey: composer.id)
            switch saved {
            case true? where readOnly:
                keptOnThisMac(composer)
            case true?:
                say("Saved to Drafts")
                draftsChanged()
                if composer.waitsForServer, let id = composer.draftId { reaching[id] = AppSeams.clock.now }
            case false?:
                failedSend = snapshot
                // An earlier save that holds all of it but what isn't an address leaves nothing to lose: the toast
                // goes by itself, as the page's offers do. Else it stays until Edit or Dismiss.
                let unsaved = composer.unsaved
                let detail = Self.unsavedWords(unsaved, readOnly: readOnly)
                showFloat(.failed("Your message couldn’t be saved", detail: detail),
                          for: unsaved.keptAllBut ? Self.offersSeconds : 0)
            case nil:
                break
            }
        }
    }

    /// How long a toast with something to do stays: the page's `OFFERS_MS`.
    static let offersSeconds = 8.0

    /// What a close that couldn't save says under its headline: why, and what an earlier save kept, if one did.
    static func unsavedWords(_ unsaved: Composer.Unsaved, readOnly: Bool) -> String {
        let kept: String
        if let at = unsaved.keptAt {
            let time = at.formatted(date: .omitted, time: .shortened)
            kept = readOnly ? "What was saved at \(time) is kept on this Mac."
                : "What was saved at \(time) is in Drafts."
        } else {
            kept = "Nothing of it was kept yet: Edit brings it back."
        }
        // The server's words may come without a full stop.
        let why = unsaved.why.map { $0.isEmpty || ".?!".contains($0.last!) ? $0 : $0 + "." }
        return [why, kept].compactMap { $0?.isEmpty == false ? $0 : nil }.joined(separator: " ")
    }

    func closed(_ composer: Composer) {
        composers.removeValue(forKey: composer.id)
        if inline?.composer === composer { inline = nil }
    }

    func send(_ composer: Composer, _ body: API.SendBody, snapshot: ComposeSeed, replyKey: Int?, after: Int,
              waitingFor save: Task<Bool?, Never>?)
    {
        failedSend = nil
        forgetBoxDrafts(of: composer.draftId)
        let scheduled = after > 0
        showFloat(scheduled ? .progress("Scheduling for \(SendTimes.words(after))…", clock: true) : .progress("↑ Sending…", clock: false))
        Task {
            var body = body, snapshot = snapshot
            if let save {
                _ = await save.value
                body = composer.sendBody(body)
                snapshot = composer.snapshot()
            }
            guard let core else { return }
            do {
                let queued = try await core.post(API.Post.Send.self, body)
                if scheduled {
                    showFloat(.done("Scheduled · \(SendTimes.words(after))", clock: true), for: 2.6)
                } else {
                    countdown(queued.outboxId, total: queued.undoSeconds, done: "✓ Sent\(queued.recipients > 0 ? " · \(queued.recipients) recipient\(queued.recipients > 1 ? "s" : "")" : "")") {
                        [weak self] in await self?.undoSend(queued.outboxId, snapshot)
                    }
                }
                if let replyKey { chips.removeValue(forKey: replyKey) }
                mailChanged?()
                outboxChanged()
            } catch let refusal as Problem where refusal.isReadOnly {
                // Read only turned it down before anything was queued, though the composer didn't know it ran read
                // only (the composer stays open when it does): the message comes back as it was, to keep, saying
                // why, and the run is read again so the next Send is turned down where it is.
                showFloat(nil)
                if let back = open(snapshot, edited: true) {
                    back.say(ReadOnlyWords.notSent, for: 5)
                } else {
                    say(ReadOnlyWords.notSent, for: 5, refused: true)
                }
                await readDigest()
            } catch {
                failedSend = snapshot
                showFloat(.failed("Send failed — \(said(error))"))
            }
        }
    }

    // MARK: The float

    private func showFloat(_ next: SendFloat?, for seconds: Double = 0) {
        floatTimer?.cancel()
        float = next
        floatLife = next == nil ? 0 : seconds
        if let next {
            switch next {
            case .countdown: break
            case .progress(let text, _), .done(let text, _):
                announce(text.replacingOccurrences(of: #"^[↑✓]\s*"#, with: "", options: .regularExpression))
            case .failed(let text, let detail):
                announce([text, detail].compactMap { $0 }.joined(separator: ". "))
            }
        }
        guard next != nil, seconds > 0 else { return }
        floatTimer = Task { [weak self] in
            try? await AppSeams.clock.sleep(for: .seconds(seconds))
            guard !Task.isCancelled else { return }
            self?.float = nil
        }
    }

    /// The seconds a queued message waits with Undo; how long is the send's own to say.
    private func countdown(_ id: Int, total: Int, done: String, undo: @escaping () async -> Void) {
        undoTimer?.cancel()
        undoing = undo
        showFloat(.countdown(id: id, left: total, total: total))
        announce("Sending. You have \(total) seconds to undo.")
        undoTimer = Task { [weak self] in
            var left = total
            while left > 0 {
                try? await AppSeams.clock.sleep(for: .seconds(1), ticking: true)
                guard let self, !Task.isCancelled else { return }
                left -= 1
                if left <= 0 {
                    self.undoing = nil
                    self.showFloat(.done(done, clock: false), for: 2.4)
                    self.outboxChanged()
                } else {
                    self.showFloat(.countdown(id: id, left: left, total: total))
                }
            }
        }
    }

    /// The float's Undo.
    func undo() {
        guard let undoing else { return }
        undoTimer?.cancel()
        self.undoing = nil
        Task { await undoing() }
    }

    /// An Undo the server said no to: it's past its window (a 409), and goes as it was going.
    private func tooLate(_ error: any Error) {
        let late = (error as? Problem)?.status == 409
        showFloat(.done(late ? "✓ Sent — too late to undo" : "✓ Sent", clock: false), for: late ? 2.2 : 2.0)
    }

    private func undoSend(_ id: Int, _ snapshot: ComposeSeed) async {
        guard let core else { return }
        do {
            _ = try await core.post(API.Post.OutboxCancel.self, API.NamesASend(id: id))
            showFloat(nil)
            open(snapshot, edited: true)
            say("Send undone")
            outboxChanged()
        } catch {
            tooLate(error)
        }
    }

    /// Something the server built and queued, with the same window to undo it in: an RSVP.
    func queued(_ id: Int, what: String, total: Int) {
        countdown(id, total: total, done: "✓ \(what) sent") { [weak self] in
            guard let self, let core = self.core else { return }
            do {
                _ = try await core.post(API.Post.OutboxCancel.self, API.NamesASend(id: id))
                self.showFloat(nil)
                self.say("\(what) not sent")
                self.outboxChanged()
            } catch {
                self.tooLate(error)
            }
        }
        outboxChanged()
    }

    /// The float's Edit: the words that failed to save or send, back in a composer.
    func editFailed() {
        guard let failed = failedSend else { return }
        failedSend = nil
        showFloat(nil)
        open(failed, edited: true)
    }

    func dismissFloat() {
        failedSend = nil
        showFloat(nil)
    }

    // MARK: Opening

    /// A composer in a window of its own. `edited`: a closed composer's words, which closing saves again.
    @discardableResult
    func open(_ seed: ComposeSeed, current: String? = nil, edited: Bool = false) -> Composer? {
        guard !senders.isEmpty else {
            say("No sending account configured — add one in Settings", refused: true)
            return nil
        }
        let composer = Composer(seed, host: self, current: current, edited: edited)
        composers[composer.id] = composer
        openWindow?(composer.id)
        return composer
    }

    /// A new message, from the account the window is in when it can send.
    func newMessage(to: [String] = [], subject: String = "", current: String? = nil) {
        var seed = ComposeSeed()
        seed.to = to
        seed.subject = subject
        open(seed, current: current)
    }

    /// The copy on screen is read for the quote; the reply names the message by its key.
    private func original(_ copy: MailCopy) async -> API.MessageOpened? {
        guard let core else { return nil }
        let read = try? await core.get(API.Get.MessageBody.self,
                                       ["key": String(copy.key), "folder": copy.folder, "uid": String(copy.uid)])
        if case .messageOpened(let opened)? = read { return opened }
        say("Couldn’t load the message")
        return nil
    }

    /// A reply in a window, or a plain reply to the message the reader offers its box for, in that box: the one open
    /// there already, the words of one closed there and saved, the reply the server says is saved to the message
    /// (`reply_draft`: kept on this Mac or in Drafts, from before MailMind last quit too), or a new one, which closes
    /// (and saves) a reply open to another message. `resuming` off always starts a new one (a quick reply's line).
    @discardableResult
    func reply(_ copy: MailCopy, all: Bool = false, resuming: Bool = true) async -> Composer? {
        let inBox = !all && inlineSlot == copy
        if inBox, let open = inline, open.copy == copy, !open.composer.closed {
            open.composer.focusRequest = .body
            return open.composer
        }
        if inBox, resuming, let closed = boxDrafts.removeValue(forKey: copy) {
            await closed.settled()
            if let open = inline, open.copy == copy, !open.composer.closed {
                open.composer.focusRequest = .body
                return open.composer
            }
            // Its words as saved go on in the box, the same draft; ones that never saved are the failed toast's.
            if closed.isSaved, inlineSlot == copy { return openInline(closed.snapshot(), for: copy) }
        }
        if resuming {   // a reply just closed is saved before the message says what answers it
            for save in Array(closingSaves.values) { _ = await save.value }
        }
        guard let opened = await original(copy) else { return nil }
        if resuming, let saved = opened.replyDraft {
            switch await reopenReply(saved, for: copy, inBox: inBox) {
            case .opened(let composer): return composer
            case .waiting: return nil
            case .gone: break
            }
        }
        let seed = Seeds.reply(opened, all: all, mine: mine)
        return inBox && inlineSlot == copy ? openInline(seed, for: copy) : open(seed)
    }

    enum Reopening {
        case opened(Composer?)
        /// It couldn't open now, and the person was told it waits in Drafts.
        case waiting
        /// It has gone since: a new reply opens.
        case gone
    }

    /// The reply saved to a message, back where the reply would open, with its words: by its id where it is
    /// MailMind's, wherever it is now, or by its copy in Drafts where it is another app's. One that can't open says it
    /// waits in Drafts rather than open an empty reply over it.
    private func reopenReply(_ saved: API.ReplyDraft, for copy: MailCopy, inBox: Bool) async -> Reopening {
        guard let core else { return .gone }
        let body = saved.inDrafts.map { API.ResumeBody(folder: $0.folder, key: $0.key, uid: $0.uid) }
            ?? API.ResumeBody(draftId: saved.draftId)
        let reopened: API.DraftOpened
        do {
            reopened = try await core.post(API.Post.DraftResume.self, body)
        } catch {
            draftsChanged()
            if (error as? Problem)?.status == 404 { return .gone }
            say(Self.replyWaits(error), refused: true)
            return .waiting
        }
        forgetBoxDrafts(of: reopened.draftId)
        var seed = Seeds.resumed(reopened)
        let more = Seeds.moreReplies(saved.more)
        seed.note = [seed.note, more].filter { !$0.isEmpty }.joined(separator: " ")
        let composer = inBox && inlineSlot == copy ? openInline(seed, for: copy) : open(seed)
        if inline?.composer === composer, !more.isEmpty {
            say(more)   // the box shows no note: the toast says it
        } else if !seed.note.isEmpty {
            announce(seed.note)
        }
        return .opened(composer)
    }

    /// What r says when the reply saved to the message can't open now: it waits in Drafts, and why.
    static func replyWaits(_ error: any Error) -> String {
        let why = (error as? Problem)?.status == 409 ? "Open it from there." : said(error)
        return "A draft reply to this message is waiting in Drafts. " + why
    }

    /// A composer for the reader's reply box: one of the composers, so everything that knows them knows it, with no
    /// window.
    private func openInline(_ seed: ComposeSeed, for copy: MailCopy) -> Composer? {
        guard !senders.isEmpty else {
            say("No sending account configured — add one in Settings", refused: true)
            return nil
        }
        if let open = inline {
            inline = nil
            open.composer.close()
        }
        let composer = Composer(seed, host: self)
        composers[composer.id] = composer
        inline = InlineReply(composer: composer, copy: copy)
        composer.focusRequest = .body
        return composer
    }

    /// A draft opened or sent from elsewhere goes on there: the box no longer opens its words as they were.
    private func forgetBoxDrafts(of draftId: Int?) {
        guard let draftId else { return }
        boxDrafts = boxDrafts.filter { $0.value.draftId != draftId }
    }

    /// The reply in the reader's box, into a window of its own, its words and saves as they are.
    func popOut() {
        guard let open = inline, !open.composer.closed else { return }
        inline = nil
        openWindow?(open.composer.id)
    }

    func forward(_ copy: MailCopy) async {
        guard let opened = await original(copy) else { return }
        open(Seeds.forward(opened, budget: attachLimit))
    }

    /// A saved draft in the composer: your text, its quote folded below, files, Bcc and thread as saved. By its copy,
    /// or by its id for one a list can't show yet.
    func editDraft(_ copy: MailCopy?, draftId: Int? = nil) async {
        guard let core else { return }
        let body = copy.map { API.ResumeBody(folder: $0.folder, key: $0.key, uid: $0.uid) } ?? API.ResumeBody(draftId: draftId)
        do {
            let reopened = try await core.post(API.Post.DraftResume.self, body)
            open(Seeds.resumed(reopened))
            forgetBoxDrafts(of: reopened.draftId)
            let note = Seeds.resumeNotice(reopened.notes)
            if !note.isEmpty { announce(note) }
        } catch {
            say(said(error).isEmpty ? "Couldn’t open this draft." : said(error), refused: true)
            if copy != nil && gone(error) { mailChanged?() }   // that copy is gone: don't keep showing it
        }
    }

    /// A quick reply: the reply with its line, then, with AI on, the AI asked, quietly, for a line or two in its place.
    /// With AI off nothing is asked of it, as the page asks nothing, and the line stays.
    func quickReply(_ copy: MailCopy, _ quick: QuickReply) async {
        // A fresh reply: its line never takes the place of words saved from the box.
        guard let composer = await reply(copy, resuming: false) else { return }
        composer.startWith(quick.line)
        guard aiOn else { return }
        let start = composer.body
        await composer.draftReply(tone: .brief, instruction: "\(quick.ask) One or two sentences.", unless: start,
                                  quiet: true)   // refused or failed: the line stays, silently
    }

    // MARK: Replies written ahead (draftAhead.ts)

    /// The message on screen: peek at a reply written ahead, which never spends; staying on it a moment, when it
    /// wants a reply, asks for one, and the event stream says when it's ready.
    func viewing(_ copy: MailCopy?, wanted: Bool, skip: Bool) {
        dwell?.cancel()
        shownCopy = nil
        guard let copy, !skip, !aiTurnedOff else { return }
        shownCopy = copy
        guard chips[copy.key] != .ready else { return }
        Task { await peek(copy) }
        if wanted {
            dwell = Task { [weak self] in
                try? await AppSeams.clock.sleep(for: Self.dwellToDraft)   // not on a fast skim
                guard let self, !Task.isCancelled, self.onScreen(copy.key), self.chips[copy.key] != .drafting else { return }
                await self.check(copy, tries: 0)
            }
        }
    }

    private func onScreen(_ key: Int) -> Bool {
        shownCopy?.key == key && composers.isEmpty
    }

    /// AI turned off: no reply written ahead is offered, waited on or asked for, so Reply is a plain reply again. An
    /// answer still on its way finds nothing on screen to give it to.
    private func forgetAhead() {
        dwell?.cancel()
        dwell = nil
        shownCopy = nil
        chips.removeAll()
    }

    private func query(_ copy: MailCopy, peek: Bool) -> KeyValuePairs<String, String?> {
        ["key": String(copy.key), "folder": copy.folder, "uid": String(copy.uid), "peek": peek ? "1" : nil]
    }

    private func peek(_ copy: MailCopy) async {
        guard !aiTurnedOff, let core,
              let read = try? await core.get(API.Get.AiDraftAhead.self, query(copy, peek: true)) else { return }
        // Turned off while it was asked: what it says isn't offered.
        guard !aiTurnedOff else { return }
        switch read {
        case .draftAheadReady(let ready) where ready.status == "ready": chips[copy.key] = .ready
        case .draftAheadStatus(let status) where status.status == "ready": chips[copy.key] = .ready
        case .draftAheadStatus(let status) where status.status == "generating" && chips[copy.key] != .drafting:
            chips[copy.key] = .drafting   // one wait per message
            await check(copy, tries: 0)
        default: break
        }
    }

    private func check(_ copy: MailCopy, tries: Int) async {
        let key = copy.key
        if chips[key] == .ready { return }
        guard onScreen(key), !aiTurnedOff else {   // moved off or turned off: never strand "Drafting…"
            chips.removeValue(forKey: key)
            return
        }
        guard let core, let read = try? await core.get(API.Get.AiDraftAhead.self, query(copy, peek: false)) else { return }
        // Turned off while it was asked: a draft that finished anyway isn't offered.
        guard !aiTurnedOff else {
            chips.removeValue(forKey: key)
            return
        }
        switch read {
        case .draftAheadReady(let ready) where ready.status == "ready" && !ready.body.isEmpty:
            let first = chips[key] != .ready
            chips[key] = .ready
            if first && onScreen(key) { announce("AI reply draft ready — press ⌘R to review") }
        case .draftAheadStatus(let status) where status.status == "generating":
            if tries < Self.liveTries && onScreen(key) {
                chips[key] = .drafting
                try? await AppSeams.clock.sleep(for: Self.liveWait)   // the stream says when; this is only a net for a lost event
                await check(copy, tries: tries + 1)
            } else {
                chips.removeValue(forKey: key)
            }
        default:
            chips.removeValue(forKey: key)
        }
    }

    /// The event stream's "draft-ready", by the key of the message it answers: read again, never trusted. A draft
    /// asked for before AI was turned off can finish after: with AI turned off it is never read.
    func draftReady(_ key: Int) {
        guard !aiTurnedOff, let viewing = shownCopy else { return }
        if viewing.key == key {
            Task { await check(viewing, tries: Self.liveTries) }
        } else if composers.isEmpty && chips[viewing.key] == nil {
            Task { await peek(viewing) }   // a sibling in the open thread may have got it
        }
    }

    /// Reply, and r: the reply written ahead if there is one, else a plain reply. With AI turned off it is always a
    /// plain reply: none written ahead, and none asked for.
    func answer(_ copy: MailCopy) async {
        // A reply already being written in the box keeps its words: the keys go back to it.
        if let open = inline, open.copy == copy, inlineSlot == copy, !open.composer.closed, open.composer.dirty {
            open.composer.focusRequest = .body
            return
        }
        // Words closed in the box and saved open there again, never a reply written ahead in their place.
        guard !aiTurnedOff, chips[copy.key] == .ready, !(inlineSlot == copy && boxDrafts[copy] != nil) else {
            await reply(copy)
            return
        }
        // Always ask again: the server checks the thread hasn't moved on, so a stale draft is never put in the box.
        guard openingAhead != copy.key else { return }
        openingAhead = copy.key
        defer { openingAhead = nil }
        guard let composer = await reply(copy) else { return }
        // The reply saved to the message opened, with its words: the AI's never take their place.
        if composer.draftId != nil { return }
        // Nor do they take the place of words typed while they were asked for.
        let start = composer.body
        var written: String?
        if let core, case .draftAheadReady(let ready)? = try? await core.get(API.Get.AiDraftAhead.self, query(copy, peek: true)),
           ready.status == "ready", !ready.body.isEmpty
        {
            written = ready.body
        }
        // Turned off while it was asked: the plain reply stands, with nothing of the AI's in it.
        guard !composer.closed, !aiTurnedOff else { return }
        if let written {
            if composer.place(written, unless: start), inline?.composer === composer { inline?.drafted = true }
        } else {
            chips.removeValue(forKey: copy.key)
            guard composer.body == start else { return }
            composer.say("Thread moved on — drafting a fresh reply…")
            await composer.draftReply(unless: start)
        }
    }

    /// A plain reply now, the draft being written left to go on.
    func replyNow(_ copy: MailCopy) async {
        chips.removeValue(forKey: copy.key)
        await reply(copy)
    }
}
#endif

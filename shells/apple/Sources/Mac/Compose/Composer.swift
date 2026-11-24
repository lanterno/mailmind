#if os(macOS)
import Foundation
import Observation
import UniformTypeIdentifiers

/// What a composer needs of the app around it: the routes, who you are, the snippets and what else has to know when
/// it saves, sends or closes. `Composing` is the app's; the tests give their own.
@MainActor
protocol ComposeHost: AnyObject {
    var routes: (any Routes)? { get }
    /// The accounts mail can go from.
    var senders: [String] { get }
    /// Your addresses, tags and all.
    var mine: Set<String> { get }
    var tone: API.DraftTone { get }
    /// What one message's files may come to, when the app knows; the server refuses past it anyway.
    var attachLimit: Int? { get }
    var aiOn: Bool { get }
    /// MailMind runs read only, where nothing is sent: Send says so in the composer, which stays as it is.
    var readOnly: Bool { get }
    /// What asks whether an AI call a person started may go over today's limit: the window's alert in the app.
    var asksOverLimit: AskOverLimit? { get }
    var snippets: [API.Snippet] { get }
    func signature(_ email: String) -> String
    func loadSnippets()
    /// Change the snippets; nil when done, else what went wrong.
    func changeSnippets(_ change: API.SnippetChange) async -> String?
    /// What the lookup said of an address before, kept for the app's life when it knew someone.
    func known(_ address: String) -> Person?
    func remember(_ address: String, _ person: Person)
    /// Epoch seconds.
    func now() -> Int
    /// Seconds on a clock that only goes forward, for how long a warning has shown.
    func clock() -> Double
    /// What a screen reader hears.
    func announce(_ text: String)
    func draftsChanged()
    func foldersChanged()
    func outboxChanged()
    /// A composer closed whose draft read only keeps on this Mac, saved before it closed: the app says where it is
    /// kept and shows it there.
    func keptOnThisMac(_ composer: Composer)
    /// A composer closed with words to save: the app says how the save went, and keeps them a click away if it
    /// failed.
    func closing(_ composer: Composer, save: Task<Bool?, Never>, snapshot: ComposeSeed)
    /// A composer handed its message over to go: the app sends it, with Undo.
    func send(_ composer: Composer, _ body: API.SendBody, snapshot: ComposeSeed, replyKey: Int?, after: Int,
              waitingFor save: Task<Bool?, Never>?)
    func closed(_ composer: Composer)
}

extension ComposeHost {
    var readOnly: Bool { false }
    func keptOnThisMac(_ composer: Composer) {}
}

/// What the send check shows: the lines, what must be waved through, and whether it may be yet.
struct GuardView: Equatable {
    struct Line: Equatable, Identifiable {
        var key: String
        var soft: Bool
        var text: String
        var fix: GuardFix?
        var field: ComposeField?
        var id: String { key }
    }

    /// What must be waved through, soft lines aside.
    var keys: [String]
    /// How many people the fan-out line counted.
    var fanN: Int
    var sendAfter: Int
    /// Nothing to wave through: there is no Send anyway.
    var hard: Bool
    var shownAt: Double
    var armed: Bool
    var lines: [Line]
    /// What the screen reader hears.
    var said: String

    static func of(_ warnings: [GuardWarning], sendAfter: Int, shownAt: Double, when: (Int) -> String) -> GuardView {
        let hard = SendGuard.hard(warnings), later = sendAfter > 0
        let lines = warnings.map { warning in
            let (text, fix) = warning.line(when: when)
            var field: ComposeField?
            if case .bad(_, _, let at) = warning { field = at }
            return Line(key: warning.key, soft: warning.block == .soft, text: text, fix: fix, field: field)
        }
        let spoken = hard ? "Press Escape to keep editing."
            : "Press Command Return to \(later ? "schedule" : "send") anyway, or Escape to keep editing."
        return GuardView(keys: warnings.filter { $0.block != .soft }.map(\.key),
                         fanN: warnings.lazy.compactMap(\.fanout).first ?? 0,
                         sendAfter: sendAfter, hard: hard, shownAt: shownAt, armed: false, lines: lines,
                         said: "\(lines.map(\.text).joined(separator: " ")) \(spoken)")
    }
}

/// One message being written, in a window of its own, as the page's compose machine (`compose/machine.ts`) runs its
/// one composer: its saves go in turn, each naming the copy the last one left; a save clears `dirty` only if no edit
/// landed while it was in flight; the send check runs synchronously, so nothing is awaited before the composer closes
/// and a double send can't happen; and what is closed with unsaved words keeps them a click away.
@MainActor
@Observable
final class Composer: Identifiable {
    enum Focus: Hashable {
        case guardStrip, send, body, subject, schedule, attach
        case field(ComposeField)
    }

    enum Busy: Equatable {
        case none, draft, rewrite
    }

    struct Preview: Equatable {
        var html: String
        /// False is no Markdown, so it goes out as plain text.
        var formatted: Bool
    }

    /// A file being read, before it is one of the attachments.
    struct Reading: Equatable, Identifiable {
        let id: UUID
        let name: String
    }

    /// A file, or a picture's bytes, to add.
    enum Source {
        case file(URL)
        case data(name: String, type: String, Data)
    }

    static let autosaveSeconds = 8.0
    static let knownWait = Duration.milliseconds(150)

    let id = UUID()
    private(set) var mode: API.ComposeMode
    private(set) var from: String
    private(set) var to: [Recipient]
    private(set) var cc: [Recipient]
    private(set) var bcc: [Recipient]
    /// What is in a field and not yet a chip.
    private(set) var typed: [ComposeField: String] = [.to: "", .cc: "", .bcc: ""]
    private(set) var showCc: Bool
    private(set) var subject: String
    private(set) var subject0: String
    private(set) var body: String
    private(set) var sigBlock: String
    /// The message a reply or forward is of, by its key.
    let origKey: Int?
    let quote: Quote?
    private(set) var draftId: Int?
    /// The save this composer last saw, so one it never saw isn't overwritten.
    private(set) var version: Int?
    /// Another window saved since: autosave stops, and only a save someone asks for overwrites.
    private(set) var conflict = false
    /// Its own threading and quote: a reopened draft, or a reply after its first save.
    private(set) var resume: ResumeFields?
    private(set) var fwdAttach: Int
    private(set) var attachments: [ComposeFile]
    private(set) var reading: [Reading] = []
    let idempotencyKey = UUID().uuidString
    private(set) var dirty = false
    /// Every edit counts: a save clears `dirty` only if none landed while it was in flight.
    private var edits = 0
    @ObservationIgnored private var saving: Task<Bool?, Never>?
    @ObservationIgnored private var lastSaveHash: Int?
    /// When its last save kept it: what a close that couldn't save says is kept.
    @ObservationIgnored private(set) var savedAt: Date?
    /// Why its last save didn't keep it, in the server's words.
    @ObservationIgnored private var unsavedWhy: String?
    /// Its last save is kept on this Mac and its server's copy is still to go: an autosave leaves that to the push
    /// job, a minute or more on, and a save the server's gate was too busy for leaves it there too.
    @ObservationIgnored private(set) var waitsForServer = false
    private(set) var savedLine = ""
    let note: String
    /// A reply-all's copied-in people.
    private(set) var autoCc: [String]?
    /// Where a Reply-To took this reply, for the check before it goes.
    private(set) var redirect: Redirect?
    private(set) var ack: SendGuard.Acks
    private(set) var check: GuardView?
    /// A Later time the check held back: the next Send schedules for it.
    private(set) var pendingAfter = 0
    /// The thread's people: never "new".
    private(set) var participants: Set<String>
    /// What the message replied to calls its people, by address.
    let names: [String: String]
    private(set) var people: [String: Person] = [:]
    private(set) var thin = false
    @ObservationIgnored private var knownQueue: [String] = []
    @ObservationIgnored private var newSay: Set<String> = []
    private(set) var undoRewrite: (value: String, selection: NSRange)?
    private(set) var busy = Busy.none
    /// The box shows how the message will read, not what you typed.
    private(set) var previewing = false
    /// Nil while it's being asked for.
    private(set) var preview: Preview?
    /// What Tab would bring in where the cursor is.
    private(set) var offer: API.Snippet?
    /// Tab goes through the {{placeholders}} a snippet brought in.
    private(set) var filling = false
    /// What this window says for a moment, as the page's toasts.
    private(set) var notice: String?
    private(set) var closed = false
    /// The Later menu, which a late time's fix opens.
    var laterOpen = false
    /// Set when a fix asks for the file panel, until the view that opens it takes the request.
    private(set) var wantsFiles = false

    /// The text view's selection, in UTF-16 offsets, as it last told.
    var selection = NSRange(location: 0, length: 0)
    /// Where the composer wants the selection and the focus; the view takes each and clears it.
    var selectionRequest: NSRange?
    var focusRequest: Focus?

    @ObservationIgnored private unowned let host: any ComposeHost
    @ObservationIgnored private var autosaver: Task<Void, Never>?
    @ObservationIgnored private var knownTimer: Task<Void, Never>?
    @ObservationIgnored private var armTimer: Task<Void, Never>?
    @ObservationIgnored private var noticeTimer: Task<Void, Never>?
    @ObservationIgnored private var previewTurn = 0

    /// A composer opened with `seed`, from the account it names when that one can send, else `current`'s, else the
    /// first. `edited`: a closed composer's words opened again, which closing saves.
    init(_ seed: ComposeSeed, host: any ComposeHost, current: String? = nil, edited: Bool = false) {
        self.host = host
        let senders = host.senders
        let from = seed.from.flatMap { senders.contains($0) ? $0 : nil }
            ?? current.flatMap { senders.contains($0) ? $0 : nil } ?? senders.first ?? ""
        self.from = from
        mode = seed.mode
        var body = seed.body ?? "", sigBlock = ""
        if let given = seed.body {
            sigBlock = Seeds.signatureKept(given, signature: host.signature(seed.from ?? from))
        } else if seed.draftId == nil && !seed.noSignature {
            let signature = host.signature(from)
            if !signature.isEmpty {
                sigBlock = "\n\n-- \n\(signature)"   // write above the signature, not after it
                body = sigBlock
            }
        }
        self.body = body
        self.sigBlock = sigBlock
        // A person's own words come back checked as they were typed: a closed composer's, or a draft saved and
        // reopened, whose chips may hold what no server would take. What the original named is taken as it is.
        let typed = edited || seed.draftId != nil
        to = seed.to.map { typed ? Recipient($0) : Recipient(trusted: $0) }
        cc = seed.cc.map { typed ? Recipient($0) : Recipient(trusted: $0) }
        bcc = seed.bcc.map { typed ? Recipient($0) : Recipient(trusted: $0) }
        showCc = !seed.cc.isEmpty || !seed.bcc.isEmpty
        subject = seed.subject
        subject0 = seed.subject0 ?? seed.subject
        origKey = seed.origKey
        quote = seed.quote
        draftId = seed.draftId
        version = seed.version
        resume = seed.resume
        fwdAttach = seed.fwdAttach
        attachments = seed.attachments
        note = seed.note
        autoCc = seed.autoCc?.map { $0.lowercased() }
        redirect = seed.redirect
        ack = seed.ack
        participants = Set(seed.participants.map { $0.lowercased() })
        names = seed.names
        if edited { markEdited() }
        host.loadSnippets()
        autosaver = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(for: .seconds(Self.autosaveSeconds))
                guard let self, !Task.isCancelled else { return }
                self.autosaveTick()
            }
        }
        queueKnown((to + cc + bcc).map(\.value), say: false)   // prefilled chips are marked silently
        // Its words as saved, opened again as they were: closing says where they are, as it would have.
        if !edited, let at = seed.saved {
            lastSaveHash = payload().hash
            savedAt = at
        }
    }

    func recipients(_ field: ComposeField) -> [Recipient] {
        switch field {
        case .to: to
        case .cc: cc
        case .bcc: bcc
        }
    }

    private func setRecipients(_ field: ComposeField, _ value: [Recipient]) {
        switch field {
        case .to: to = value
        case .cc: cc = value
        case .bcc: bcc = value
        }
    }

    private var all: [Recipient] { to + cc + bcc }

    private func markEdited() {
        dirty = true
        edits += 1
    }

    /// Say something in this window for a moment (longer words for longer), and to a screen reader.
    func say(_ text: String, for seconds: Double = 2.6) {
        notice = text
        host.announce(text)
        noticeTimer?.cancel()
        noticeTimer = Task { [weak self] in
            try? await Task.sleep(for: .seconds(seconds))
            guard !Task.isCancelled else { return }
            self?.notice = nil
        }
    }

    // MARK: Closing

    /// What a closed composer's words come back as: failed to save, or an Undo Send.
    func snapshot() -> ComposeSeed {
        var seed = ComposeSeed()
        seed.mode = mode
        seed.from = from
        seed.to = to.map(\.value)
        seed.cc = cc.map(\.value)
        seed.bcc = bcc.map(\.value)
        seed.subject = subject
        seed.subject0 = subject0
        seed.origKey = origKey
        seed.quote = quote
        seed.fwdAttach = fwdAttach
        seed.draftId = draftId
        seed.version = version
        seed.resume = resume
        seed.noSignature = true
        seed.autoCc = autoCc
        seed.redirect = redirect
        seed.participants = Array(participants)
        seed.names = names
        seed.ack = ack
        seed.body = body
        seed.attachments = attachments
        seed.saved = isSaved ? savedAt : nil
        return seed
    }

    /// Its words as they stand are a draft this composer saved, or opened as saved: nothing is left to save.
    var isSaved: Bool { lastSaveHash != nil && !dirty }

    /// Once the saves it has begun have ended: each waits for the one before, so the last one ends last.
    func settled() async {
        if let saving { _ = await saving.value }
    }

    private func stopTimers() {
        autosaver?.cancel()
        knownTimer?.cancel()
        armTimer?.cancel()
    }

    /// Close it, saving what's unsaved unless `force`.
    func close(force: Bool = false) {
        guard !closed else { return }
        stopTimers()
        laterOpen = false
        if !force { commitTyped() }
        let closingSave = dirty && !force
        // Saved already, by the autosave a moment ago, and unchanged since: closing still says where the words went,
        // so a reply closed with Esc isn't closed in silence.
        let savedBefore = !force && !closingSave && isSaved
        // Saved by an autosave only, which leaves its server's copy to the push job a minute or more on: closing
        // saves it as a save someone asks for does, which tries its server now, so Drafts has it in seconds.
        let reaches = savedBefore && waitsForServer && !host.readOnly
        if closingSave || savedBefore {
            // The save read the fields: the snapshot holds them.
            let saved = closingSave || reaches ? save(silent: true) : Task<Bool?, Never> { true }
            host.closing(self, save: saved, snapshot: snapshot())
        }
        closed = true
        if draftId != nil && !closingSave && !savedBefore {
            host.draftsChanged()   // its saves replaced the copy that list shows
            // Read only kept its saves on this Mac, where no list shows them: closing says where it is.
            if host.readOnly { host.keptOnThisMac(self) }
        }
        host.closed(self)
    }

    /// What a close that couldn't save says of it: why, and what an earlier save kept.
    struct Unsaved: Equatable {
        /// The first recipient that isn't an address, as the send check says it; else the server's answer.
        let why: String?
        /// When an earlier save kept it, if one did.
        let keptAt: Date?
        /// That save holds all of it but the recipients that aren't addresses.
        let keptAllBut: Bool
    }

    var unsaved: Unsaved {
        var why = unsavedWhy
        if let problem = recipientProblem(), case .bad = problem { why = problem.line().text }
        let good = payload(droppingBad: true)
        return Unsaved(why: why, keptAt: lastSaveHash == nil ? nil : savedAt,
                       keptAllBut: lastSaveHash != nil && lastSaveHash == good.hash)
    }

    /// Save what's unsaved as MailMind quits, but leave it open: a person may yet choose to stay. Nil when there is
    /// nothing to save. It is kept on this Mac only, as an autosave is: the core answers once its journal holds it,
    /// rather than after trying its server for a while, and the copy goes to the server when MailMind next opens.
    func saveBeforeQuitting() -> Task<Bool?, Never>? {
        guard !closed else { return nil }
        commitTyped()
        guard dirty else { return nil }
        return save(silent: true, autosave: true)
    }

    /// Throw it away, the copy on the server too. Read only turns that down where the server may hold a copy: the
    /// draft stays, so the composer stays open with its words and says why.
    func discard() async {
        guard !closed else { return }
        if let saving { _ = await saving.value }   // a first save still in flight would leave its copy behind
        if let draftId, let routes = host.routes {
            do {
                _ = try await routes.post(API.Post.Draft.self, API.DraftBody(delete: true, draftId: draftId))
            } catch let refusal as Problem where refusal.isReadOnly {
                say(ReadOnlyWords.refused, for: 5)
                return
            } catch {}
            host.foldersChanged()
        }
        dirty = false
        close(force: true)
    }

    // MARK: Editing

    /// Any real edit retires the check: Send checks again from scratch.
    private func retireGuard() {
        guard let shown = check else { return }
        if shown.keys.contains("reading") && !reading.isEmpty {   // still reading: the line stays, naming what's left
            let text = GuardWarning.reading(name: reading[0].name, more: reading.count - 1).line().text
            var kept = shown
            kept.lines = shown.lines.map { $0.key == "reading" ? GuardView.Line(key: $0.key, soft: $0.soft, text: text,
                                                                                fix: $0.fix, field: $0.field) : $0 }
            kept.said = "\(text) Press Escape to keep editing."
            check = kept
            return
        }
        hideGuard()
    }

    private func hideGuard() {
        armTimer?.cancel()
        check = nil
    }

    func setSubject(_ value: String) {
        guard value != subject else { return }
        subject = value
        markEdited()
        retireGuard()
    }

    /// The text as the text view now holds it, with its selection.
    func setBody(_ value: String, selection: NSRange) {
        self.selection = selection
        guard value != body else { return }
        body = value
        noteOffer()
        markEdited()
        retireGuard()
    }

    func setFrom(_ value: String) {
        guard value != from else { return }
        from = value
        let signature = host.signature(value), block = signature.isEmpty ? "" : "\n\n-- \n\(signature)"
        if sigBlock.isEmpty ? !block.isEmpty : body.hasSuffix(sigBlock) {   // a signature you edited is yours, and stays
            let units = body as NSString
            let keep = units.length - (sigBlock as NSString).length
            body = units.substring(to: keep) + block
            selectionRequest = NSRange(location: min(selection.location, keep),
                                       length: max(0, min(NSMaxRange(selection), keep) - min(selection.location, keep)))
            leavePreview()
        }
        sigBlock = block
        markEdited()
        retireGuard()
    }

    func setTyped(_ field: ComposeField, _ value: String) {
        guard typed[field] != value else { return }
        typed[field] = value
        markEdited()
        retireGuard()
    }

    func commitChip(_ field: ComposeField, _ raw: String) {
        let added = Addresses.split(raw)
        if !added.isEmpty {
            setRecipients(field, recipients(field) + added.map { Recipient($0) })
            markEdited()
        }
        typed[field] = ""
        queueKnown(added, say: true)
        queueKnown(all.map(\.value), say: false)   // a lookup that failed is asked again
        retireGuard()
    }

    private func commitTyped() {
        for field in ComposeField.allCases where !(typed[field] ?? "").trimmingCharacters(in: .whitespaces).isEmpty {
            commitChip(field, typed[field] ?? "")
        }
    }

    func removeChip(_ field: ComposeField, at index: Int) {
        var list = recipients(field)
        guard list.indices.contains(index) else { return }
        list.remove(at: index)
        setRecipients(field, list)
        markEdited()
        retireGuard()
        focusRequest = .field(field)
    }

    func showCopies() {
        showCc = true
    }

    // MARK: Snippets

    private func noteOffer() {
        let word = selection.length == 0 ? Snippets.wordBefore(body, caret: selection.location) : nil
        offer = word.flatMap { Snippets.offered(host.snippets, word: $0.word) }
    }

    /// The text view's selection moved.
    func caretMoved(_ selection: NSRange) {
        self.selection = selection
        noteOffer()
    }

    /// Words into the text between two places, the cursor on the first placeholder they hold, or after them if none.
    private func bringIn(_ range: NSRange, _ text: String) {
        let units = body as NSString
        body = units.replacingCharacters(in: range, with: text)
        let after = range.location + (text as NSString).length
        let first = Snippets.after(body, at: range.location)
        let here = first.flatMap { NSMaxRange($0) <= after ? $0 : nil }
        offer = nil
        filling = here != nil
        leavePreview()
        markEdited()
        retireGuard()
        focusRequest = .body
        selectionRequest = here ?? NSRange(location: after, length: 0)
    }

    func insertSnippet(_ snippet: API.Snippet) {
        bringIn(inBody(selection), snippet.text)
    }

    /// A range inside the text as it is now: the composer may have changed the text since the selection was taken.
    private func inBody(_ range: NSRange) -> NSRange {
        let length = (body as NSString).length, start = min(range.location, length)
        return NSRange(location: start, length: max(0, min(NSMaxRange(range), length) - start))
    }

    /// Tab in the text: brings in what the word before the cursor names, then goes through what that left to fill
    /// in. Otherwise it is the key it always was, and past the last placeholder it moves on, so it never holds anyone
    /// in the box. Says if it did anything.
    func tabInBody(back: Bool) -> Bool {
        let at = selection.location, end = NSMaxRange(selection)
        if !back, selection.length == 0, let word = Snippets.wordBefore(body, caret: at),
           let snippet = Snippets.offered(host.snippets, word: word.word)
        {
            bringIn(NSRange(location: word.from, length: at - word.from), snippet.text)
            return true
        }
        guard filling else { return false }
        if let next = back ? Snippets.before(body, at: at) : Snippets.after(body, at: end) {
            selectionRequest = next
            return true
        }
        filling = false
        return false
    }

    var selectedText: String {
        let units = body as NSString
        guard NSMaxRange(selection) <= units.length else { return "" }
        return units.substring(with: selection)
    }

    // MARK: Write and Preview

    private func leavePreview() {
        previewing = false
        preview = nil
    }

    func showPreview() async {
        guard !previewing, let routes = host.routes else { return }
        previewTurn += 1
        let turn = previewTurn
        previewing = true
        do {
            let read = try await routes.post(API.Post.ComposePreview.self,
                                             API.PreviewBody(pictures: Pictures.parts(attachments), text: body))
            guard !closed, turn == previewTurn, previewing else { return }
            preview = Preview(html: Pictures.inline(read.html, files: attachments), formatted: read.formatted)
        } catch {
            guard !closed, turn == previewTurn else { return }
            leavePreview()
            say("Couldn’t show a preview: \(said(error))")
        }
    }

    func write() {
        guard previewing else { return }
        leavePreview()
        focusRequest = .body
    }

    // MARK: "New to you": a soft mark, never a block

    private func queueKnown(_ values: [String], say: Bool) {
        let mine = host.mine
        for value in values {
            let address = Addresses.recipient(value)
            if address.isEmpty || !Addresses.looksLikeAddress(address) || SendGuard.isMine(address, mine)
                || participants.contains(address) { continue }
            if let cached = host.known(address) {
                people[address] = cached
                continue
            }
            if people[address] != nil { continue }
            if !knownQueue.contains(address) { knownQueue.append(address) }
            if say { newSay.insert(address) }
        }
        guard !knownQueue.isEmpty else { return }
        knownTimer?.cancel()
        knownTimer = Task { [weak self] in
            try? await Task.sleep(for: Self.knownWait)
            guard !Task.isCancelled else { return }
            await self?.flushKnown()
        }
    }

    /// One request for every queued address, never one per chip.
    private func flushKnown() async {
        guard !closed, let routes = host.routes, !knownQueue.isEmpty else { return }
        let asked = Array(knownQueue.prefix(100))
        knownQueue.removeFirst(asked.count)
        let read: API.Known
        do {
            read = try await routes.post(API.Post.ContactsKnown.self, API.KnownBody(addrs: asked.map { .string($0) }))
        } catch {
            return   // pending or failed: no mark at all
        }
        guard !closed else { return }
        thin = read.thin
        for (address, verdict) in read.items {
            let person = Person(known: verdict.known, isSelf: verdict.self_, near: verdict.near)
            people[address] = person
            if person.known || person.isSelf { host.remember(address, person) }
        }
        let hits = newSay.filter { isNew($0) }.sorted()
        newSay.removeAll()
        if !hits.isEmpty && check == nil { host.announce("New to you: \(SendGuard.list(hits)).") }
        if !knownQueue.isEmpty { await flushKnown() }
    }

    /// Someone the lookup says you've never dealt with, and not one of the thread's people.
    func isNew(_ address: String) -> Bool {
        guard let person = people[address] else { return false }
        return !person.known && !person.isSelf && !thin && !SendGuard.isMine(address, host.mine)
            && !participants.contains(address)
    }

    private func knownHere(_ address: String) -> Bool {
        let person = people[address]
        return participants.contains(address) || person?.known == true || person?.isSelf == true
    }

    /// Recipients the lookup said read as someone you've written to, mistyped: each once.
    func nearMisses() -> [SendGuard.NearMiss] {
        var found: [SendGuard.NearMiss] = []
        for recipient in all where !recipient.bad {
            let address = recipient.address
            if let near = people[address]?.near, !SendGuard.isMine(address, host.mine), !participants.contains(address),
               !found.contains(where: { $0.addr == address })
            {
                found.append(.init(addr: address, near: near))
            }
        }
        return found
    }

    // MARK: Files

    /// Add files. `inText`: a picture goes where the cursor is, as a mark the text shows it by, there before the file
    /// has been read so the words around it don't wait; any other kind of file is an attachment like the rest.
    func add(_ sources: [Source], inText: Bool = false) {
        var total = attachments.reduce(0) { $0 + $1.size }
        var at = inBody(selection)
        var marked = false
        for source in sources {
            let name: String, type: String, size: Int
            switch source {
            case .file(let url):
                name = url.lastPathComponent
                type = UTType(filenameExtension: url.pathExtension)?.preferredMIMEType ?? "application/octet-stream"
                size = (try? url.resourceValues(forKeys: [.fileSizeKey]).fileSize) ?? 0
            case .data(let named, let kind, let data):
                (name, type, size) = (named, kind, data.count)
            }
            if let limit = host.attachLimit, total + size > limit {
                say("Attachments too large (\(limit / 1_048_576) MB max)")
                continue
            }
            total += size
            let id = UUID()
            let cid = inText && Pictures.isPicture(type)
                ? String(id.uuidString.filter { $0.isLetter || $0.isNumber }.prefix(16)) : nil
            if let cid {
                let mark = Pictures.mark(name, cid: cid)
                body = (body as NSString).replacingCharacters(in: at, with: mark)
                at = NSRange(location: at.location + (mark as NSString).length, length: 0)
                marked = true
            }
            reading.append(Reading(id: id, name: name))
            Task { [weak self] in
                let read: Data? = await Task.detached {
                    switch source {
                    case .file(let url): try? Data(contentsOf: url)
                    case .data(_, _, let data): data
                    }
                }.value
                guard let self else { return }
                self.reading.removeAll { $0.id == id }
                if let read {
                    self.attachments.append(ComposeFile(filename: name, contentType: type, size: read.count,
                                                        base64: read.base64EncodedString(), cid: cid))
                    self.markEdited()
                    if !self.closed { self.retireGuard() }
                } else {
                    if let cid { self.body = Pictures.withoutMark(self.body, cid: cid) }
                    if !self.closed {
                        self.say("Couldn’t add \(name)")
                        self.retireGuard()
                    }
                }
            }
        }
        if marked {
            leavePreview()
            markEdited()
            retireGuard()
            selectionRequest = at
        }
    }

    func removeAttachment(at index: Int) {
        guard attachments.indices.contains(index) else { return }
        let gone = attachments.remove(at: index)
        if let cid = gone.cid { body = Pictures.withoutMark(body, cid: cid) }   // a picture's mark goes with it
        markEdited()
        retireGuard()
    }

    // MARK: Saving

    /// What a save or a send says, as the server takes it.
    private struct Payload {
        var account: String
        var mode: API.ComposeMode
        var key: String
        var draftId: Int?
        var to: [String], cc: [String], bcc: [String]
        var subject: String
        var text: String
        var originalKey: Int?
        var attachments: [ComposeFile]
        var resume: ResumeFields?

        var hash: Int {
            var hasher = Hasher()
            hasher.combine(to)
            hasher.combine(cc)
            hasher.combine(bcc)
            hasher.combine(subject)
            hasher.combine(text)
            hasher.combine(account)
            hasher.combine(attachments.map(\.filename))
            return hasher.finalize()
        }

        func draft(autosave: Bool, baseVersion: Int?) -> API.DraftBody {
            API.DraftBody(account: account, attachments: attachments.map(\.json), autosave: autosave ? true : nil,
                          baseVersion: baseVersion, bcc: .strings(bcc), cc: .strings(cc), draftId: draftId,
                          idempotencyKey: key, mode: mode, originalKey: originalKey, resume: resume?.json,
                          subject: subject, text: text, to: .strings(to))
        }

        func send(after: Int) -> API.SendBody {
            API.SendBody(account: account, attachments: attachments.map(\.json), bcc: .strings(bcc), cc: .strings(cc),
                         draftId: draftId, idempotencyKey: key, mode: mode, originalKey: originalKey,
                         resume: resume?.json, sendAfter: after, subject: subject, text: text, to: .strings(to))
        }
    }

    /// What a save sends; `droppingBad`, without the recipients that aren't addresses.
    private func payload(droppingBad: Bool = false) -> Payload {
        let kept = { (list: [Recipient]) in (droppingBad ? list.filter { !$0.bad } : list).map(\.value) }
        return Payload(account: from, mode: mode, key: idempotencyKey, draftId: draftId, to: kept(to), cc: kept(cc),
                       bcc: kept(bcc), subject: subject, text: body, originalKey: origKey, attachments: attachments,
                       resume: resume)
    }

    /// A payload built before the save it waited for: that save may have switched the composer to naming itself.
    private func namesItself(_ payload: inout Payload) {
        guard let resume, payload.resume == nil else { return }
        payload.resume = resume
        payload.attachments = attachments
    }

    /// Never litter Drafts with an empty or signature-only message.
    var nothingToSave: Bool {
        let written = (sigBlock.isEmpty ? body : body.replacingOccurrences(of: sigBlock, with: ""))
            .trimmingCharacters(in: .whitespacesAndNewlines)
        return to.isEmpty && cc.isEmpty && bcc.isEmpty && subject.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            && written.isEmpty
    }

    /// A reply's or forward's first save: from now on it names its quote, threading and files itself, so the
    /// original's row can go.
    private func adopt(_ saved: API.DraftSaved) {
        guard let handed = saved.resume, resume == nil else { return }
        resume = ResumeFields(inReplyTo: handed.inReplyTo, references: handed.references, tailText: handed.tailText)
        guard fwdAttach > 0 else { return }
        attachments += (saved.files ?? []).map {   // the server no longer adds them: they are this composer's now
            ComposeFile(filename: $0.filename, contentType: $0.contentType, size: $0.size, base64: $0.b64, cid: $0.cid)
        }
        fwdAttach = 0
    }

    /// Save: true once saved, false if it wasn't, nil when there's nothing to save. Half an address isn't a recipient
    /// yet, so autosave leaves what's being typed where it is; a save anyone asked for takes it.
    @discardableResult
    func save(silent: Bool = false, takeTyped: Bool = true, autosave: Bool = false) -> Task<Bool?, Never> {
        if takeTyped { commitTyped() }
        if nothingToSave {
            if !silent { say("Nothing to save yet") }
            return Task<Bool?, Never> { nil }
        }
        var payload = payload()
        let at = edits, prior = saving
        let task = Task { [self] () -> Bool? in
            if let prior { _ = await prior.value }   // one composer's saves go in turn
            payload.draftId = draftId
            namesItself(&payload)
            let base = version != nil && (silent || !conflict) ? version : nil   // a save someone asked for overwrites
            guard let routes = host.routes else { return false }
            do {
                let answer = try await routes.post(API.Post.Draft.self, payload.draft(autosave: autosave, baseVersion: base))
                switch answer {
                case .draftSaved(let saved):
                    draftId = saved.draftId
                    version = saved.version
                    adopt(saved)
                    waitsForServer = !saved.pushed
                    if !autosave && !saved.pushed { host.outboxChanged() }   // it waits for its server: the tray says so
                case .nothingToSave(let nothing):
                    draftId = nothing.draftId ?? draftId
                case .draftDiscarded:
                    break
                }
                conflict = false
                if edits == at { dirty = false }   // an edit since this save began stays to save
                lastSaveHash = payload.hash
                savedAt = Date()
                unsavedWhy = nil
                savedLine = "Saved \(Date().formatted(date: .omitted, time: .shortened))"
                if !silent { say("Draft saved") }
                return true
            } catch let problem as Problem where problem.status == 409 {
                conflict = true
                unsavedWhy = "This draft changed in another window."
                savedLine = "Changed in another window"
                if !silent { say("This draft changed in another window. Save again to keep this text.") }
                return false
            } catch {
                unsavedWhy = said(error)
                savedLine = "Not saved"   // never leave an older "Saved" up
                if !silent { say("Couldn’t save draft: \(said(error))") }
                return false
            }
        }
        saving = task
        Task { [weak self] in
            _ = await task.value
            if self?.saving == task { self?.saving = nil }
        }
        return task
    }

    /// Every few seconds: save what changed since the last save, unless one is going or another window saved since.
    func autosaveTick() {
        guard !closed, dirty, saving == nil, !conflict, !nothingToSave else { return }
        if payload().hash != lastSaveHash { save(silent: true, takeTyped: false, autosave: true) }
    }

    // MARK: Sending

    /// What stops a send before the checks proper: nobody to send it to, or a chip that isn't an address.
    private func recipientProblem() -> GuardWarning? {
        if all.isEmpty { return .norcpt }
        let bad = ComposeField.allCases.flatMap { field in recipients(field).filter(\.bad).map { (field, $0.value) } }
        guard let first = bad.first else { return nil }
        return .bad(value: first.1, more: bad.count - 1, field: first.0)
    }

    func snapshotForChecks(sendAfter: Int) -> SendGuard.Snapshot {
        let mine = host.mine
        let selves = people.filter { $0.value.isSelf }.map(\.key)
        let fresh = Array(Set(all.filter { !$0.bad }.map(\.address))).filter { isNew($0) }.sorted()
        return SendGuard.Snapshot(
            mode: mode, subject: subject, subject0: subject0, body: body, sigBlock: sigBlock, nAttach: attachments.count,
            fwdAttach: fwdAttach, reading: reading.map(\.name), sendAfter: sendAfter, now: host.now(),
            to: to.map(\.address), cc: cc.map(\.address), mine: Array(mine) + selves, autoCc: autoCc, fresh: fresh,
            near: nearMisses(),
            redirect: redirect.map { .init(to: $0.to, sender: $0.sender, stranger: !knownHere($0.to)) }, ack: ack)
    }

    private func runChecks(_ sendAfter: Int) -> [GuardWarning] {
        SendGuard.check(snapshotForChecks(sendAfter: sendAfter))
    }

    private func showGuard(_ warnings: [GuardWarning], sendAfter: Int, lead: String = "") {
        let shown = GuardView.of(warnings, sendAfter: sendAfter, shownAt: host.clock(), when: { SendTimes.words($0) })
        check = shown
        armTimer?.cancel()
        if !shown.hard {
            armTimer = Task { [weak self] in
                try? await Task.sleep(for: .seconds(SendGuard.armSeconds))
                guard let self, !Task.isCancelled, self.check?.shownAt == shown.shownAt else { return }
                self.check?.armed = true
            }
        }
        focusRequest = .guardStrip
        host.announce(lead.isEmpty ? shown.said : "\(lead) \(shown.said)")
    }

    /// Escape, Keep editing: back to where you were.
    func dismissGuard() {
        guard let shown = check else { return }
        hideGuard()
        focusRequest = shown.sendAfter > 0 ? .schedule : .body
    }

    /// Send, by its button or ⌘Return: through the send guard, or past a check it shows where it may be. `held`: a
    /// press that only repeats one (a held key's auto-repeat, a double click's second), which never sends: ⌘Return
    /// held from before a check showed would otherwise wave it through once its wait was over.
    func pressSend(held: Bool = false) {
        if check != nil { return confirmGuard(repeated: held) }
        guard !held else { return }
        send(after: pendingAfter)
    }

    /// Send anyway. `repeated`: a double click or a held key, which never confirms.
    func confirmGuard(repeated: Bool = false) {
        guard let shown = check else { return }
        if shown.hard {   // nothing to wave through: say why again
            if !repeated {
                focusRequest = .guardStrip
                host.announce(shown.said)
            }
            return
        }
        if repeated || host.clock() - shown.shownAt < SendGuard.armSeconds { return }
        let now = runChecks(shown.sendAfter)
        let confirms = now.filter { $0.block == .confirm }
        let fan = now.lazy.compactMap(\.fanout).first ?? 0
        if SendGuard.hard(now) || confirms.contains(where: { !shown.keys.contains($0.key) }) || fan > shown.fanN {
            showGuard(now, sendAfter: shown.sendAfter, lead: "Something changed.")   // the time passed, or an edit
            return
        }
        for key in shown.keys {
            switch key {
            case "fanout": ack.fanout = max(ack.fanout, shown.fanN)
            case "near":
                for case .near(let pairs) in now { ack.near += pairs.map(\.addr) }
            case "subject": ack.subject = true
            case "attach": ack.attach = true
            case "unchecked": ack.unchecked = true
            case "redirect": ack.redirect = true
            default: break
            }
        }
        hideGuard()
        send(after: shown.sendAfter)
    }

    /// Keep only the Cc you added yourself: it's a plain reply after that.
    private func trimCc() {
        guard let auto = autoCc else { return }
        let before = cc.count
        cc = cc.filter { !auto.contains($0.address) }
        let removed = before - cc.count
        mode = .reply
        markEdited()
        hideGuard()
        focusRequest = .send
        let left = SendGuard.fanout(to: to.map(\.address), cc: cc.map(\.address), mine: Array(host.mine))
        host.announce("Removed \(removed) \(removed == 1 ? "person" : "people"). This reply goes to \(left) \(left == 1 ? "person" : "people").")
    }

    /// A fix that names the right address: the chip keeps its name and takes that one.
    private func swapAddress(_ swap: GuardFix.Swap) {
        let named = { (value: String) -> String in
            value.range(of: #"<[^<>]*>\s*$"#, options: .regularExpression) != nil
                ? value.replacingOccurrences(of: #"<[^<>]*>\s*$"#, with: "<\(swap.to)>", options: .regularExpression)
                : swap.to
        }
        for field in ComposeField.allCases {
            setRecipients(field, recipients(field).map { $0.address == swap.from ? Recipient(named($0.value)) : $0 })
        }
        markEdited()
        hideGuard()
        focusRequest = .send
        host.announce("Changed to \(swap.to).")
    }

    /// A line's fix.
    func fix(_ fix: GuardFix, field: ComposeField?) {
        if fix.kind == "trim" { return trimCc() }
        if let swap = fix.swap { return swapAddress(swap) }
        hideGuard()
        switch fix.kind {
        case "review":
            showCc = true
            focusRequest = .field(.cc)
        case "bad":
            guard let field, let index = recipients(field).firstIndex(where: \.bad) else { return }
            var list = recipients(field)
            typed[field] = list.remove(at: index).value   // back in the field, to fix it
            setRecipients(field, list)
            if field != .to { showCc = true }
            focusRequest = .field(field)
        case "subject": focusRequest = .subject
        case "attach":
            focusRequest = .attach
            wantsFiles = true
        case "time": laterOpen = true
        case "to": focusRequest = .field(.to)
        default: break
        }
    }

    /// Whether a fix asked for the file panel, which the view that opens it takes once: left set, the request would
    /// never change again, so a later one would open nothing.
    func takeFilesRequest() -> Bool {
        guard wantsFiles else { return false }
        wantsFiles = false
        return true
    }

    /// Send, now or at `after` (epoch seconds). The checks run here and now; past them the composer closes and the
    /// app sends what it held.
    func send(after: Int = 0) {
        guard !closed else { return }
        pendingAfter = max(after, 0)   // kept if a check holds this attempt
        commitTyped()
        if host.readOnly {
            // Nothing can go: the composer stays open as it is, and says why where the person is looking.
            hideGuard()
            return say(ReadOnlyWords.notSent, for: 5)
        }
        if let problem = recipientProblem() { return showGuard([problem], sendAfter: after) }
        let warnings: [GuardWarning] = runChecks(after)
        if SendGuard.blocks(warnings) { return showGuard(warnings, sendAfter: after) }
        hideGuard()
        let body = payload().send(after: after > 0 ? after : 0)
        let replyKey = mode == .reply || mode == .replyall ? origKey : nil
        let snapshot = snapshot()   // Undo, then send it unchanged, isn't asked again
        // A send for later leaves its draft in Drafts until it goes, and there to send another time if it is
        // cancelled: saved now as a save someone asks for is, which tries its server at once, so the copy there is
        // what was scheduled. Else a save still in flight: the send must name the copy it leaves.
        let waiting = after > 0 ? save(silent: true) : saving
        dirty = false
        close(force: true)
        host.send(self, body, snapshot: snapshot, replyKey: replyKey, after: after, waitingFor: waiting)
    }

    /// The send's body once the save it waited for has landed: that save named the draft, and may have switched the
    /// composer to naming itself.
    func sendBody(_ body: API.SendBody) -> API.SendBody {
        var updated = body
        updated.draftId = draftId
        if let resume, body.resume == nil {
            updated.resume = resume.json
            updated.attachments = attachments.map(\.json)
        }
        return updated
    }

    // MARK: The AI's words

    /// Whether a reply may be drafted: a reply to a message whose quote isn't already in the box.
    var aiOffered: Bool {
        (mode == .reply || mode == .replyall) && origKey != nil && !(resume != nil && quote == nil)
    }

    private func placeBody(_ text: String) {
        body = text
        leavePreview()
        markEdited()
        retireGuard()
    }

    /// Draft the reply. `unless`: a quick reply's line, which wins once you've changed it. `quiet`: no one pressed for
    /// it, so it never asks at the daily limit.
    func draftReply(tone: API.DraftTone? = nil, instruction: String = "", unless: String? = nil, quiet: Bool = false) async {
        guard busy == .none, let routes = host.routes else { return }
        guard let origKey else { return say("Open a reply to draft one") }
        busy = .draft
        defer { busy = .none }
        do {
            let drafted = try await routes.post(API.Post.AiDraftReply.self, API.DraftReplyBody(
                account: from, instruction: instruction, key: origKey, tone: tone ?? host.tone),
                asking: quiet ? nil : host.asksOverLimit)
            guard !closed else { return }   // closed while drafting: not this composer's text
            if let unless, body != unless { return }   // a reply you've started writing since: yours wins
            // No words came back: what is in the box stays, rather than leave only the signature to go.
            guard !drafted.body.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
                if !quiet { say("Couldn’t draft a reply") }
                return
            }
            let trimmed = body.trimmingCharacters(in: .whitespacesAndNewlines)
            if !trimmed.isEmpty && trimmed != sigBlock.trimmingCharacters(in: .whitespacesAndNewlines) {
                undoRewrite = (body, selection)   // what you'd written is one Undo away
            }
            placeBody(drafted.body + sigBlock)   // the signature stays below the AI's reply
            focusRequest = .body
            selectionRequest = NSRange(location: (drafted.body as NSString).length, length: 0)   // above the signature
            say("Draft ready — review and edit before sending")
        } catch {
            if !closed && !quiet { say("Couldn’t draft: \(said(error))") }
        }
    }

    /// Rewrite the selection, or the whole message.
    func rewrite(_ op: API.RewriteOp, language: String = "English") async {
        guard busy == .none, let routes = host.routes else { return }
        let full = body, range = selection, units = full as NSString
        let hasSelection = range.length > 0 && NSMaxRange(range) <= units.length
        let text = hasSelection ? units.substring(with: range) : full
        guard !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            return say("Write something to rewrite first")
        }
        busy = .rewrite
        defer { busy = .none }
        do {
            let rewritten = try await routes.post(API.Post.AiRewrite.self,
                                                  API.RewriteBody(lang: language, op: op, text: text),
                                                  asking: host.asksOverLimit)
            guard !closed else { return }
            undoRewrite = (full, range)   // one step back to what it was
            placeBody(hasSelection ? units.replacingCharacters(in: range, with: rewritten.text) : rewritten.text)
            focusRequest = .body
            if hasSelection {
                selectionRequest = NSRange(location: range.location, length: (rewritten.text as NSString).length)
            }
            say("\(hasSelection ? "Selection" : "Message") rewritten")
        } catch {
            if !closed { say("Couldn’t rewrite: \(said(error))") }
        }
    }

    func undoTheRewrite() {
        guard let (value, range) = undoRewrite else { return }
        body = value
        undoRewrite = nil
        leavePreview()
        retireGuard()
        focusRequest = .body
        selectionRequest = range
        say("Back to what you had")
    }

    /// A quick reply's line in the box, as written by you: nothing to save until you touch it.
    func startWith(_ line: String) {
        body = line + sigBlock
        focusRequest = .body
        selectionRequest = NSRange(location: (line as NSString).length, length: 0)
    }

    /// A draft written ahead, in the box, unless the box no longer holds `unless`: words typed while it was asked
    /// for are the person's, and stay. Says whether it went in.
    @discardableResult
    func place(_ text: String, unless: String? = nil) -> Bool {
        if let unless, body != unless { return false }
        placeBody(text + sigBlock)
        focusRequest = .body
        selectionRequest = NSRange(location: (text as NSString).length, length: 0)   // above the signature
        say("Draft ready — review and edit before sending")
        return true
    }
}
#endif

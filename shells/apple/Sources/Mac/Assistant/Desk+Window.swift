#if os(macOS)
import AppKit
import Observation
import SwiftUI

/// The assistant, curation, attention and the calendar in one window: their models over the window's core, what they
/// need of the window (the message chosen, where the window is, opening a message), and the sheets they open. The
/// window holds one, made with it; its models reach the core once it is open.
@MainActor
@Observable
final class Desk {
    enum Sheet: Identifiable {
        case ask
        case plan
        case review
        case commitments
        case curate
        case filter(CurateModel.FilterForm)
        case snoozeUntil([MailCopy], canUnsnooze: Bool)

        var id: String {
            switch self {
            case .ask: "ask"
            case .plan: "plan"
            case .review: "review"
            case .commitments: "commitments"
            case .curate: "curate"
            case .filter: "filter"
            case .snoozeUntil: "snooze"
            }
        }

        /// Whether it says the window's toast on itself while it is up (`DeskSheetToast`), so the window's own,
        /// under it, waits: Curate's, whose verbs say what they did with Undo.
        var saysToasts: Bool {
            if case .curate = self { return true }
            return false
        }

        /// Whether it floats over the window (`WindowOverlays`) rather than opening as a sheet: Ask and a bulk action
        /// in the palette, the snooze chooser as a popover.
        var floats: Bool {
            switch self {
            case .ask, .plan, .snoozeUntil: true
            case .review, .commitments, .curate, .filter: false
            }
        }
    }

    @ObservationIgnored let link = CoreLink(asking: { await OverLimitQuestion.onScreen.ask($0) })
    let notices = DeskNotices()
    let summaries: SummaryCards
    let suggestions: SuggestionCards
    let asking: AskModel
    let plan: PlanModel
    let brief: BriefModel
    let tasks: TasksModel
    let review: ReviewModel
    let attention: AttentionModel
    let curate: CurateModel
    let invites: InviteReplies
    /// A sentence typed in the search field, turned into the search it means.
    let prose: ProseSearch
    /// Mail that landed away from the list on screen, and a line on what came in since the Inbox was looked at.
    let since: SinceLooked
    private(set) var screeners: [String: ScreenerModel] = [:]
    var sheet: Sheet?

    @ObservationIgnored private(set) weak var app: MailApp?
    /// Shows a search, where the window has one to show (a plan that reads as a search offers it).
    @ObservationIgnored var showSearch: ((String) -> Void)?
    /// Opens a reply to a message, where the window has a composer (the brief's Reply).
    @ObservationIgnored var reply: ((MailCopy) -> Void)?
    @ObservationIgnored private var listening: Task<Void, Never>?

    init() {
        summaries = SummaryCards(link: link)
        suggestions = SuggestionCards(link: link)
        asking = AskModel(link: link)
        plan = PlanModel(link: link, notices: notices)
        brief = BriefModel(link: link, notices: notices)
        tasks = TasksModel(link: link, notices: notices)
        review = ReviewModel(link: link)
        attention = AttentionModel(link: link, notices: notices)
        curate = CurateModel(link: link, notices: notices)
        invites = InviteReplies(link: link, notices: notices)
        prose = ProseSearch(link: link)
        since = SinceLooked(link: link)
        let readAgain: () -> Void = { [weak self] in self?.readAgain() }
        plan.changed = readAgain
        brief.changed = readAgain
        attention.changed = readAgain
        // Snoozed, or done with in Reply Later, the message open moves on as File moves on; and filed from a card, as
        // a commitment's Done files it.
        attention.leaving = { [weak self] copies in self?.app?.mailbox.leaving(copies) ?? {} }
        brief.leaving = { [weak self] copies in self?.app?.mailbox.leaving(copies) ?? {} }
        // A snooze from a row's clock or the brief says which message it was.
        attention.naming = { [weak self] copies in self?.app?.mailbox.subjectAway(copies) }
        // A conversation's row snoozes the whole conversation, as the page's does.
        attention.conversation = { [weak self] copies in self?.app?.mailbox.conversation(of: copies) }
        curate.changed = readAgain
        curate.openPage = { NSWorkspace.shared.open($0) }
        // Edit ▸ Undo on Curate's sheet takes back what the mailbox's still holds, as its toast's Undo does: once
        // between them, so neither undoes something older once the other has.
        curate.undoFromSheet = { [weak self] name in
            guard let mailbox = self?.app?.mailbox else { return }
            if let undo = mailbox.undoManager {
                guard undo.canUndo, undo.undoActionName == name else { return }
            }
            mailbox.undo()
        }
        brief.paperTrailQuery = { [weak self] in self?.paperTrail?.search?.q }
        // The tallies count the stacks they open (`goToStack`).
        brief.stacksAccount = { [weak self] in self?.stacksAccount }
    }

    /// Reach the window's core once it is open, and hear what changes; and hang the desk's hooks on the mailbox, again
    /// when another window let go of them as it closed.
    func attach(_ app: MailApp) {
        guard let core = app.core else { return }
        if self.app == nil {
            self.app = app
            connect(core)
            listening = Task { [weak self] in
                guard let events = try? await core.events() else { return }
                for await event in events {
                    guard let self else { return }
                    await self.heard(event)
                }
            }
            Task { await readScreeners() }
        }
        join(app.mailbox)
        doAsked()
    }

    /// A test's window and routes, with no core: the desk reads the window it is given, speaks those routes, and hangs
    /// its hooks on the window's mailbox, as `attach` does once a core is open.
    func use(_ app: MailApp, routes: any RouteCaller) {
        self.app = app
        connect(routes)
        join(app.mailbox)
        doAsked()
    }

    /// What the menus asked of the mail window while none was open (Ask, Your Week, Curate…), which opened it again:
    /// the next desk to join a window does it, once. A window's desk is its own, made as the window opens.
    static var asked: [@MainActor (Desk) -> Void] = []

    private func doAsked() {
        let asked = Self.asked
        Self.asked = []
        for act in asked { act(self) }
    }

    /// Reach the core's routes, and read what was asked for before they were there.
    func connect(_ routes: any RouteCaller) {
        link.routes = routes
        // The rail asks for the brief's tallies as it first shows, which can be before the window's core is open.
        brief.reread()
    }

    /// The desk in the mailbox: `?` and `!` in the search field, a sentence searched for, the list's h, w, l and i, the
    /// palette's and a row's menu's commands, and the notices said where the mailbox says its own. The brief's Reply
    /// opens the composer, and the status menu's Curate the curate sheet.
    private func join(_ mailbox: Mailbox) {
        mailbox.join("desk") { [weak self] mailbox in
            mailbox.asks = { words in Task { await self?.ask(words) } }
            mailbox.plans = { words in Task { await self?.plan(words) } }
            mailbox.translates = { [weak mailbox] typed in
                await self?.prose.translate(typed, aiOn: mailbox?.aiOn ?? false)
            }
            mailbox.keepsLiteral = { self?.prose.keepLiteral($0) }
            mailbox.translating = { self?.prose.translating }
            mailbox.stopsTranslating = { self?.prose.stop() }
            for key: Character in ["h", "w", "l", "i"] {
                mailbox.keys[key] = {
                    guard let self else { return }
                    self.commands(for: self.chosenCopies, stack: self.stack).first { $0.key == key }?.run()
                }
            }
            mailbox.more.append { _ in
                guard let self else { return [] }
                let onChosen = self.commands(for: self.chosenCopies, stack: self.stack).map {
                    MailCommand(id: $0.id, group: .actions, label: $0.label, hint: $0.key.map(String.init), run: $0.run)
                }
                return onChosen + self.paletteCommands.map {
                    MailCommand(id: $0.id, group: .commands, label: $0.label, run: $0.run)
                }
            }
            // A row's snooze button asks when, as the row's menu does.
            mailbox.snoozes = { rows in self?.askWhen(rows.map(MailCopy.init)) }
            mailbox.opensBrief = { self?.openBrief(deliberately: $0) }
            mailbox.undoing = { self?.undoing() ?? { _ in } }
            mailbox.wentAway = { self?.wentAway($0) }
            mailbox.rereadsTallies = { self?.brief.reread() }
            mailbox.inDesk = { view in self.map { AnyView(view.environment($0)) } ?? view }
            mailbox.doesAsked = { self?.doAsked() }
            mailbox.rowMenu.append { rows in
                guard let self else { return [] }
                return self.commands(for: rows.map(MailCopy.init), stack: self.stack).map { command in
                    Item(command.label, command.symbol, run: command.run)
                }
            }
            // What can be taken back is on Edit ▸ Undo too, as the mailbox's own verbs are, so ⌘Z, z and the toast's
            // Undo are one undo.
            self?.notices.relay = { [weak mailbox] text, undo, refused in
                mailbox?.say(text, undo: undo, refused: refused)
                if let undo { mailbox?.offerUndo(undo) }
            }
        }
        showSearch = { [weak app] q in app?.place = .search(Search(q: q)) }
        reply = { to in Task { await Composing.shared.answer(to) } }
        AppStatus.shared.curate = { [weak self] in self?.sheet = .curate }
    }

    func heard(_ event: CoreEvent) async {
        // New mail is told of first: nothing read again below holds it up. Mail that lands in the list on screen shows
        // there, the Inbox's beside the brief too, so the pill says only what landed elsewhere.
        if event.name == "new-mail", let said = event.decoded(SinceLooked.Arrived.self) {
            since.newMail(said, on: app?.listed, ai: app?.mailbox.aiOn ?? false)
        }
        if event.name == "snooze-wake", let said = event.decoded(SinceLooked.Woke.self) {
            since.snoozeWake(said, on: app?.listed)
        }
        if MailApp.mailChanged.contains(event.name) {
            brief.reread()
            await readScreeners()
            await review.reread()
        }
        // Summaries landed: what the brief, its line over the Inbox and the rail count as needing you changed, though
        // no sender came to any gate.
        if event.name == MailApp.summarized { brief.reread() }
        if event.name == "tasks-changed" { await tasks.load() }
    }

    // MARK: The window

    /// The message the reader has open.
    var chosen: MailCopy? { app?.selection.copy }

    /// What a verb acts on: the rows marked in the list, else the message open.
    var chosenCopies: [MailCopy] {
        let marked = app?.mailbox.marked ?? []
        return marked.isEmpty ? chosen.map { [$0] } ?? [] : marked
    }

    /// The attention stack the window's list is, if it is one.
    var stack: AttentionStack? {
        switch app?.place {
        case .virtual(let folder, _)?: AttentionStack(rawValue: folder.rawValue)
        case .folder(_, let folder)?: AttentionStack(folder: folder)
        default: nil
        }
    }

    var accounts: [String] { app?.mailbox.accounts ?? [] }

    /// Every account's Inbox unread, as the rail counts it, for the brief's line; none until the counts are read.
    var inboxesUnread: Int? { app?.mailbox.inboxesUnread }

    /// AI was turned off in Settings: the desk offers nothing only AI can do, and asks no one to turn it on.
    var aiTurnedOff: Bool { app?.mailbox.aiTurnedOff ?? false }

    /// An account's name as the sidebar shows it.
    func nameOf(_ account: String) -> String {
        app?.mailbox.name(of: account) ?? account
    }

    /// Open a message in the reader, wherever it is: no row chosen before stays chosen, so the verbs act on this one.
    func open(_ copy: MailCopy) {
        app?.mailbox.open(copy)
    }

    func go(_ place: Place) {
        app?.place = place
    }

    /// Show today's brief in the pane. Opened on purpose (a click or Return on its row) it writes today's when none is
    /// stored, as opening it does; arrived at by the arrow keys it shows what is stored and offers to write the rest.
    func openBrief(deliberately: Bool) {
        guard app?.place != .desk(.brief) else {
            if deliberately, brief.brief == nil, !brief.writing { Task { await brief.load() } }
            return
        }
        if !deliberately { brief.glance() }
        go(.desk(.brief))
    }

    /// The paper trail's lane, as the rail's views open it, where the server's views have one.
    var paperTrail: Place? {
        guard let mailbox = app?.mailbox,
              let view = mailbox.views.first(where: { Lane(view: $0.id) == .papertrail }) else { return nil }
        return .search(Search(q: viewQuery(view), account: mailbox.searchAccount, lane: .papertrail))
    }

    /// A stack as the brief's tallies open it: the account the window is in, else every account's.
    func goToStack(_ stack: AttentionStack) {
        guard let folder = VirtualFolder(rawValue: stack.rawValue) else { return }
        go(.virtual(folder, account: stacksAccount))
    }

    /// The account the window is in, for the brief's stacks: the brief is a place of no account, so it is the one
    /// whose Inbox it stands beside (`MailApp.listed`), as the page's `goToFolder` opens one account's from beside its
    /// Inbox; nil beside All Inboxes, for every account's.
    var stacksAccount: String? {
        app?.place?.account ?? app?.listed?.account
    }

    /// Read the lists, the sidebar and what is open here again, as the page's `readAgain` does after a change.
    func readAgain() {
        app?.mailbox.readAgain()
        brief.reread()
        Task { [weak self] in
            await self?.readScreeners()
            await self?.review.reread()
        }
    }

    // MARK: Asking

    /// A question asked of your mail ("?…" in the search field), answered in the list's place.
    func ask(_ question: String) async {
        goToAnswer()
        await asking.ask(question)
    }

    /// To the answer in the list's place, remembering where the person was, which Esc goes back to
    /// (`Mailbox.leaveAsk`).
    func goToAnswer() {
        if let place = app?.place, place != .desk(.ask) { app?.mailbox.askedFrom = place }
        go(.desk(.ask))
    }

    func startPlan() {
        plan.start()
        sheet = .plan
    }

    /// A plan said in the window's search field ("!…"), shown in its sheet.
    func plan(_ command: String) async {
        sheet = .plan
        await plan.plan(command)
    }

    func newFilter(_ query: String = "") {
        sheet = .filter(.new(query))
    }

    /// Ask when to bring `copies` back; in Snoozed, bringing them back now is offered too.
    func askWhen(_ copies: [MailCopy]) {
        sheet = .snoozeUntil(copies, canUnsnooze: stack == .snoozed)
    }

    // MARK: Commands

    /// The commands the pane's bar offers on what the reader shows (its Snooze and Later): on the message in the pane
    /// alone, however it was opened, whatever rows are marked beside it, which the keys and the bulk bar act on.
    var paneCommands: [DeskCommand] {
        commands(for: chosen.map { [$0] } ?? [], stack: stack)
    }

    /// The commands only AI can serve, by id: none is offered while AI is turned off.
    static let aiCommands: Set<String> = ["summarize", "extract", "ask", "do", "week"]

    /// `commands` as offered: without the AI's while AI is turned off.
    static func offered(_ commands: [DeskCommand], aiTurnedOff: Bool) -> [DeskCommand] {
        aiTurnedOff ? commands.filter { !aiCommands.contains($0.id) } : commands
    }

    /// The commands on the messages chosen, for the mailbox's single keys, its palette and a row's menu: attention's
    /// verbs in the list `stack` is, and on one message, the AI's summary (i) and its events and tasks.
    func commands(for copies: [MailCopy], stack: AttentionStack?) -> [DeskCommand] {
        var all = AttentionCommands.list(copies: copies, stack: stack, model: attention) { [weak self] in
            self?.sheet = .snoozeUntil($0, canUnsnooze: stack == .snoozed)
        }
        if copies.count == 1, let copy = copies.first {
            all.append(DeskCommand(id: "summarize", label: String(localized: "AI summarize"), key: "i", symbol: "sparkles") {
                [weak self] in Task { await self?.summaries.summarize(copy) }
            })
            all.append(DeskCommand(id: "extract", label: String(localized: "Find events and tasks in this message"),
                                   key: nil, symbol: "calendar.badge.plus") {
                [weak self] in Task { await self?.suggestions.find(in: copy) }
            })
        }
        return Self.offered(all, aiTurnedOff: aiTurnedOff)
    }

    /// The desk's commands that act on no message, for the palette, in the page's words.
    var paletteCommands: [DeskCommand] {
        Self.offered(allPaletteCommands, aiTurnedOff: aiTurnedOff)
    }

    private var allPaletteCommands: [DeskCommand] {
        [
            DeskCommand(id: "brief", label: String(localized: "Today’s brief"), key: nil, symbol: "sun.max") {
                [weak self] in self?.go(.desk(.brief))
            },
            DeskCommand(id: "ask", label: String(localized: "Ask your mail…"), key: nil, symbol: "sparkles") {
                [weak self] in self?.sheet = .ask
            },
            DeskCommand(id: "do", label: String(localized: "Do… — natural-language bulk action"), key: nil,
                        symbol: "wand.and.stars") { [weak self] in self?.startPlan() },
            DeskCommand(id: "commitments", label: String(localized: "Commitments and tasks"), key: nil,
                        symbol: "checklist") { [weak self] in self?.sheet = .commitments },
            DeskCommand(id: "week", label: String(localized: "Your week"), key: nil, symbol: "calendar") {
                [weak self] in self?.sheet = .review
            },
            DeskCommand(id: "curate", label: String(localized: "Curate — rules and audit"), key: nil,
                        symbol: "slider.horizontal.3") { [weak self] in self?.sheet = .curate },
            DeskCommand(id: "cleanup", label: String(localized: "Clean up senders (unsubscribe)"), key: nil,
                        symbol: "person.crop.circle.badge.xmark") { [weak self] in self?.sheet = .curate },
            DeskCommand(id: "new-filter", label: String(localized: "New filter — star or mark read new mail"), key: nil,
                        symbol: "line.3.horizontal.decrease.circle") { [weak self] in self?.newFilter() },
        ]
    }

    /// The last action taken back, as the page's Undo does it (`/api/undo`).
    func undo() async {
        guard let routes = link.routes else { return }
        let answered = undoing()
        var answer: API.Undone?
        defer { answered(answer) }
        do {
            let undone = try await routes.post(API.Post.Undo.self, API.NoBody())
            answer = undone
            if let failed = undone.failed, failed > 0 {
                notices.say(String(localized: "\(failed) couldn’t be restored — Undo again to try again"))
            } else {
                notices.say(String(localized: "\(undone.label.flatMap { $0.isEmpty ? nil : $0 } ?? String(localized: "Action")) undone"))
            }
        } catch let problem as Problem {
            notices.say(problem.isReadOnly ? ReadOnlyWords.refused
                : problem.more["empty"] != nil ? String(localized: "Nothing to undo")
                : problem.detail ?? String(localized: "Couldn’t undo"), refused: true)
        } catch {
            notices.say(String(localized: "Couldn’t undo"), refused: true)
        }
        readAgain()
    }

    /// An Undo is asked, from the mailbox's toast or the desk's notice: what it puts back in an Inbox is never new
    /// mail, so the pill leaves it out. Curate, open, reads its senders and rules again once it lands, as a cleanup
    /// undone floods the Inbox again.
    func undoing() -> @MainActor (API.Undone?) -> Void {
        since.undoing()
        return { [weak self] done in
            guard let self else { return }
            since.undone(account: done?.account, folder: done?.folder, count: done.map { $0.reversed ?? 1 } ?? 0)
            if done != nil, case .curate? = sheet {
                Task { await self.curate.load() }
            }
        }
    }

    // MARK: Screeners

    func screener(for account: String) -> ScreenerModel {
        if let held = screeners[account] { return held }
        let made = ScreenerModel(account: account, link: link, notices: notices)
        made.changed = { [weak self] in self?.readAgain() }
        screeners[account] = made
        return made
    }

    /// Each account's Screener, for the sidebar's counts, and the gate's card with it, as the mail changed.
    func readScreeners() async {
        for account in accounts {
            await screener(for: account).reload()
        }
    }

    /// Mail a verb sent away from where it was (the gate's "Read it whole", then Delete): the brief leaves it out at
    /// once, and a gate whose card shows one of them reads its queue and its card's letter again, so the card goes
    /// with its sender's last message, or shows the next.
    func wentAway(_ copies: [MailCopy]) {
        brief.left(copies)
        let gone = Set(copies)
        for screener in screeners.values where screener.shows(any: gone) {
            Task { await screener.reload() }
        }
    }
}

extension FocusedValues {
    /// The key window's desk, for the menu bar's commands.
    @Entry var mailDesk: Desk?
}

/// What the desk adds to the window, in one modifier: the desk in the environment and the menu bar's reach, and the
/// sheets that don't float over the window.
struct DeskSurfaces: ViewModifier {
    let app: MailApp
    @State private var desk = Desk()

    func body(content: Content) -> some View {
        content
            .environment(desk)
            .focusedSceneValue(\.mailDesk, desk)
            // Ask, a bulk action and the snooze chooser float over the window (`WindowOverlays`), as its notice does.
            .sheet(item: Binding(get: { desk.sheet?.floats == false ? desk.sheet : nil },
                                 set: { desk.sheet = $0 })) { sheet in
                DeskSheetView(sheet: sheet, desk: desk)
            }
            .task(id: app.stage) {
                if app.stage == .open { desk.attach(app) }
            }
            // Another window let go of the mailbox's hooks as it closed: this one's desk hangs its own again.
            .onChange(of: app.mailbox.joinRound) {
                if app.stage == .open { desk.attach(app) }
            }
            // An Inbox opened, or the app come back to with one on screen, is a look; away, a line not yet written is.
            // The brief beside the Inbox shows the Inbox's list, so going to it is a look at that Inbox.
            .onChange(of: app.place, initial: true) { _, _ in
                desk.since.lookedAt(app.listed, accounts: desk.accounts)
            }
            .onReceive(NotificationCenter.default.publisher(for: NSApplication.didBecomeActiveNotification)) { _ in
                desk.since.lookedAt(app.listed, accounts: desk.accounts)
                desk.since.cameBack()
            }
            // AI's settings changed: the brief reads how AI stands, and AI just turned on writes what AI-off left out.
            .onReceive(NotificationCenter.default.publisher(for: .mailmindAIChanged)) { _ in desk.brief.aiChanged() }
    }
}

private struct DeskSheetView: View {
    let sheet: Desk.Sheet
    let desk: Desk

    var body: some View {
        switch sheet {
        case .ask, .plan, .snoozeUntil:
            EmptyView()
        case .review:
            ReviewSheet(model: desk.review, open: desk.open)
        case .commitments:
            CommitmentsSheet(tasks: desk.tasks, brief: desk.brief, attention: desk.attention, open: desk.open)
                .task { if !desk.brief.opened { await desk.brief.refresh() } }
        case .curate:
            CurateSheet(model: desk.curate, nameOf: desk.nameOf, open: { account in
                desk.go(.folder(account: account, folder: "INBOX"))
            }, mailbox: desk.app?.mailbox)
        case .filter(let form):
            FilterSheet(form: form, model: desk.curate)
        }
    }
}

/// The cards under a message's or a conversation's subject (the reader's `.top` slot): its summary and what it
/// proposes, both the AI's, so neither while AI is turned off. The verbs on the message are the pane's bar's
/// (Mac/Meadow) and the Message menu's.
struct DeskTopCards: View {
    let context: ReaderContext
    @Environment(Desk.self) private var desk: Desk?

    var body: some View {
        if let desk, !desk.aiTurnedOff {
            let copy = context.copy
            // The summary ingest stored on the message's row, when the row is this message's.
            let stored = context.row.flatMap { MailCopy($0) == copy ? (summary: $0.summary, action: $0.action) : nil }
            VStack(spacing: 14) {
                SummaryCard(copy: copy, stored: stored, cards: desk.summaries,
                            author: context.opened?.fromName ?? context.row?.fromName,
                            authorAddress: context.opened?.fromAddr ?? context.row?.fromAddr)
                SuggestionCard(copy: copy, cards: desk.suggestions, notices: desk.notices)
            }
        }
    }
}

/// A message's invite, with RSVP (the reader's `.message` slot).
struct DeskInviteCard: View {
    let context: ReaderContext
    @Environment(Desk.self) private var desk: Desk?

    var body: some View {
        if let desk, let opened = context.opened, let invite = opened.invite {
            InviteCard(copy: context.copy, invite: invite, part: opened.invitePart, replies: desk.invites,
                       link: desk.link, notices: desk.notices,
                       next: Self.next(of: invite, in: context.copy,
                                       upcoming: desk.brief.upcoming + AppStatus.shared.upcoming))
        }
    }

    /// A series' next time, where Today & next lists this message's (the brief's list, or the reminder's, read each
    /// minute): the core works out when a series falls next, and lists the soonest few. A single event's is its own.
    static func next(of invite: API.Invite, in copy: MailCopy, upcoming: [API.UpcomingEvent]) -> API.UpcomingEvent? {
        guard !invite.rrule.isEmpty || !(invite.rdates ?? []).isEmpty else { return nil }
        return upcoming.first { MailCopy(key: $0.key, folder: $0.folder, uid: $0.uid, account: $0.account) == copy }
    }
}

/// A desk place: the brief in the reading pane, an answer where the list stands, a Screener across both.
struct DeskScreen: View {
    let place: DeskPlace
    @Environment(Desk.self) private var desk: Desk?

    var body: some View {
        if let desk {
            switch place {
            case .brief:
                BriefScreen(model: desk.brief, attention: desk.attention, actions: BriefActions(
                    open: desk.open, goToStack: desk.goToStack, commitments: { desk.sheet = .commitments },
                    week: { desk.sheet = .review }, reply: desk.reply, askUntil: { desk.askWhen($0) },
                    draftReady: { Composing.shared.chips[$0] == .ready },
                    paperTrail: desk.paperTrail.map { place in { desk.go(place) } }), tasks: desk.tasks,
                    name: BriefWords.firstName(NSFullUserName()), unread: desk.inboxesUnread)
                    .task {
                        await desk.tasks.load()
                        await desk.brief.load()
                    }
                    .onDisappear { desk.brief.close() }
            case .ask:
                AskScreen(model: desk.asking, reading: desk.chosen, open: desk.open) { desk.sheet = .ask }
            case .screener(let account):
                ScreenerScreen(model: desk.screener(for: account),
                               name: desk.accounts.count > 1 ? desk.nameOf(account) : nil, accounts: desk.accounts,
                               suggests: !desk.aiTurnedOff, open: desk.open)
            }
        }
    }
}

extension Desk {
    /// What the desk adds to the rail (Mac/Mailbox's `RailPlaces`): today's brief, what it says needs you, and each
    /// account's Screener with the senders waiting in it, which the open rail shows while some wait.
    var rail: DeskRail {
        // Each message, as the Inbox's list counts them one by one where it doesn't show conversations.
        let needing = BriefWords.everyNeed(brief.items).map { item in
            DeskRail.Need(account: item.account, conversation: item.threadId ?? "message \(item.key)")
        }
        return DeskRail(brief: true, needing: needing, gates: accounts.map { account in
            DeskRail.Gate(account: account, waiting: screeners[account]?.waiting ?? 0)
        })
    }
}

/// The attention verbs and the AI's on the messages chosen, in the mailbox's Message menu; in a message's own window,
/// attention's on its message alone.
struct DeskMessageItems: View {
    /// Whether a mail window is open: with none, its desk handed on is a closed window's (`MailWindowDoor`).
    var open = true
    /// The message window in front, as the Message menu found it (`KeyWindow`).
    var window: MessageWindowVerbs?
    @FocusedValue(\.mailDesk) private var focusedDesk: Desk?

    private var desk: Desk? { open ? focusedDesk : nil }

    /// What attention's items act on in the window in front, and through which model: a message window's message,
    /// in no stack, through its own; else the mail window's choice, in the stack its list is, through its desk's.
    static func attending(window: MessageWindowVerbs?, desk: Desk?)
        -> (copies: [MailCopy], stack: AttentionStack?, model: AttentionModel)?
    {
        if let window { return window.acting ? ([window.copy], nil, window.attention) : nil }
        guard let desk, !desk.chosenCopies.isEmpty else { return nil }
        return (desk.chosenCopies, desk.stack, desk.attention)
    }

    var body: some View {
        if let on = Self.attending(window: window, desk: desk) {
            AttentionMenuItems(copies: on.copies, stack: on.stack, model: on.model, shortcuts: true)
            if window == nil, let desk, on.copies.count == 1, let copy = on.copies.first, !desk.aiTurnedOff {
                Divider()
                Button("Summarize", systemImage: "sparkles") { Task { await desk.summaries.summarize(copy) } }
                    .keyboardShortcut("i", modifiers: [.shift, .command])
                Button("Find Events and Tasks", systemImage: "calendar.badge.plus") {
                    Task { await desk.suggestions.find(in: copy) }
                }
            }
        } else {
            Button("Snooze") {}.disabled(true)
            Button("Mute Thread") {}.disabled(true)
            Button("Reply Later") {}.disabled(true)
        }
    }
}

/// The menu bar's Assistant menu: the assistant's and curation's panels; Ask, a bulk action and your week only while
/// AI isn't turned off.
struct DeskCommands: Commands {
    @Environment(\.openWindow) private var openWindow

    var body: some Commands {
        CommandMenu("Assistant") {
            AssistantMenuItems(status: .shared, door: .ofThisApp(openWindow))
        }
    }

    /// Whether the menu offers what only AI serves. The menu stays in the menu bar whichever window is key, and
    /// Settings, a composer or Help focuses no desk, so the app's word counts as much as the mail window's: neither
    /// may say AI is turned off.
    static func offersAI(_ desk: Desk?, _ ai: API.AiStatus?) -> Bool {
        desk?.aiTurnedOff != true && ai?.isTurnedOff != true
    }
}

/// The Assistant menu's items: a view, so they follow how AI stands as it changes, whatever window is in front. With
/// the mail window closed, Today's Brief opens it again on the brief, and a panel, which hangs on the window, opens
/// it again with the panel over it.
struct AssistantMenuItems: View {
    let status: AppStatus
    var door = MailWindowDoor.alwaysOpen
    @FocusedValue(\.mailDesk) private var focusedDesk: Desk?

    var body: some View {
        let open = door.isOpen()
        let desk = open ? focusedDesk : nil
        let ai = DeskCommands.offersAI(desk, status.digest?.ai)
        let brief = briefGoing(desk, open: open)
        Button("Today’s Brief", systemImage: "sun.max") { brief?() }
            .keyboardShortcut("b", modifiers: [.option, .command])
            .disabled(brief == nil)
        if ai {
            let ask = doing(desk, open: open) { $0.sheet = .ask }
            Button("Ask Your Mail…", systemImage: "sparkles") { ask?() }
                .keyboardShortcut("a", modifiers: [.option, .command])
                .disabled(ask == nil)
            // ⌥⌘D hides the Dock everywhere, so Bulk Action takes ⌥⌘K, beside the palette's ⌘K.
            let plan = doing(desk, open: open) { $0.startPlan() }
            Button("Bulk Action…", systemImage: "wand.and.stars") { plan?() }
                .keyboardShortcut("k", modifiers: [.option, .command])
                .disabled(plan == nil)
        }
        Divider()
        let commitments = doing(desk, open: open) { $0.sheet = .commitments }
        Button("Commitments and Tasks", systemImage: "checklist") { commitments?() }
            .disabled(commitments == nil)
        if ai {
            let week = doing(desk, open: open) { $0.sheet = .review }
            Button("Your Week", systemImage: "calendar") { week?() }
                .disabled(week == nil)
        }
        Divider()
        let curate = doing(desk, open: open) { $0.sheet = .curate }
        Button("Curate — Rules and Senders…", systemImage: "slider.horizontal.3") { curate?() }
            .disabled(curate == nil)
        let filter = doing(desk, open: open) { $0.newFilter() }
        Button("New Filter…", systemImage: "line.3.horizontal.decrease.circle") { filter?() }
            .disabled(filter == nil)
    }

    /// What a panel's item does: the window's desk opens it; with no mail window, the window opens again and its desk
    /// opens it there (`Desk.asked`).
    private func doing(_ desk: Desk?, open: Bool, _ act: @escaping @MainActor (Desk) -> Void)
        -> (@MainActor () -> Void)?
    {
        if open {
            guard let desk else { return nil }
            return { act(desk) }
        }
        guard let going = door.going(from: nil) else { return nil }
        let door = door
        return {
            going.go { mailbox in
                Desk.asked.append(act)
                // Drawn with no window open, and one has opened since: its desk does it now.
                if door.isOpen() { mailbox.doesAsked?() }
            }
        }
    }

    /// What Today's Brief does: the window's desk goes to it; with no mail window, the window opens again on it.
    private func briefGoing(_ desk: Desk?, open: Bool) -> (@MainActor () -> Void)? {
        if open {
            guard let desk else { return nil }
            return { desk.go(.desk(.brief)) }
        }
        guard let going = door.going(from: nil) else { return nil }
        return {
            going.go { mailbox in mailbox.app?.place = .desk(.brief) }
        }
    }
}
#else
import SwiftUI

/// The desk is the Mac's: iPhone and iPad have no menu bar commands for it yet.
struct DeskCommands: Commands {
    var body: some Commands { EmptyCommands() }
}
#endif

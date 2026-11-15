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
    }

    @ObservationIgnored let link = CoreLink()
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
        let readAgain: () -> Void = { [weak self] in self?.readAgain() }
        plan.changed = readAgain
        brief.changed = readAgain
        attention.changed = readAgain
        curate.changed = readAgain
        curate.openPage = { NSWorkspace.shared.open($0) }
    }

    /// Reach the window's core once it is open, and hear what changes.
    func attach(_ app: MailApp) {
        guard self.app == nil, let core = app.core else { return }
        self.app = app
        link.routes = core
        listening = Task { [weak self] in
            guard let events = try? await core.events() else { return }
            for await event in events {
                guard let self else { return }
                await self.heard(event)
            }
        }
        Task { await readScreeners() }
        join(app.mailbox)
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
            mailbox.rowMenu.append { rows in
                guard let self else { return [] }
                return self.commands(for: rows.map(MailCopy.init), stack: self.stack).map { command in
                    Item(command.label, command.symbol, run: command.run)
                }
            }
            self?.notices.relay = { [weak mailbox] text, undoable in
                mailbox?.say(text, undo: undoable ? String(localized: "Undo") : nil)
            }
        }
        showSearch = { [weak app] q in app?.place = .search(Search(q: q)) }
        reply = { to in Task { await Composing.shared.answer(to) } }
        AppStatus.shared.curate = { [weak self] in self?.sheet = .curate }
    }

    private func heard(_ event: CoreEvent) async {
        if MailApp.mailChanged.contains(event.name) {
            await readScreeners()
            if brief.opened { await brief.refresh() }
        }
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

    /// An account's name as the sidebar shows it.
    func nameOf(_ account: String) -> String {
        app?.mailbox.name(of: account) ?? account
    }

    /// Open a message in the reader, wherever it is.
    func open(_ copy: MailCopy) {
        guard let app else { return }
        app.selection.copy = copy
        app.reader.open(copy)
    }

    func go(_ place: Place) {
        app?.place = place
    }

    /// A stack as the brief's tallies open it: the account the window is in, else every account's.
    func goToStack(_ stack: AttentionStack) {
        guard let folder = VirtualFolder(rawValue: stack.rawValue) else { return }
        go(.virtual(folder, account: app?.place?.account))
    }

    /// Read the lists, the sidebar and what is open here again, as the page's `readAgain` does after a change.
    func readAgain() {
        app?.mailbox.readAgain()
        Task { [weak self] in
            if self?.brief.opened == true { await self?.brief.refresh() }
            await self?.readScreeners()
        }
    }

    // MARK: Asking

    /// A question asked of your mail ("?…" in the search field), answered in the list's place.
    func ask(_ question: String) async {
        go(.desk(.ask))
        await asking.ask(question)
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

    /// The commands on the messages chosen, for the mailbox's single keys, its palette and a row's menu: attention's
    /// verbs in the list `stack` is, and on one message, Claude's summary (i) and its events and tasks.
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
        return all
    }

    /// The desk's commands that act on no message, for the palette, in the page's words.
    var paletteCommands: [DeskCommand] {
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
        do {
            let undone = try await routes.post(API.Post.Undo.self, API.NoBody())
            if let failed = undone.failed, failed > 0 {
                notices.say(String(localized: "\(failed) couldn’t be restored — Undo again to try again"))
            } else {
                notices.say(String(localized: "\(undone.label.flatMap { $0.isEmpty ? nil : $0 } ?? String(localized: "Action")) undone"))
            }
        } catch let problem as Problem {
            notices.say(problem.more["empty"] != nil ? String(localized: "Nothing to undo")
                : problem.detail ?? String(localized: "Couldn’t undo"))
        } catch {
            notices.say(String(localized: "Couldn’t undo"))
        }
        readAgain()
    }

    // MARK: Screeners

    func screener(for account: String) -> ScreenerModel {
        if let held = screeners[account] { return held }
        let made = ScreenerModel(account: account, link: link, notices: notices)
        made.changed = { [weak self] in self?.readAgain() }
        screeners[account] = made
        return made
    }

    /// Each account's Screener, for the sidebar's counts.
    func readScreeners() async {
        for account in accounts {
            await screener(for: account).load()
        }
    }
}

extension FocusedValues {
    /// The key window's desk, for the menu bar's commands.
    @Entry var mailDesk: Desk?
}

/// What the desk adds to the window, in one modifier: the desk in the environment and the menu bar's reach, the
/// notice at the window's foot, and the sheets.
struct DeskSurfaces: ViewModifier {
    let app: MailApp
    @State private var desk = Desk()

    func body(content: Content) -> some View {
        content
            .environment(desk)
            .focusedSceneValue(\.mailDesk, desk)
            .overlay(alignment: .bottom) {
                DeskNoticeBanner(notices: desk.notices) { Task { await desk.undo() } }
                    .animation(.snappy, value: desk.notices.shown)
            }
            .sheet(item: $desk.sheet) { sheet in
                DeskSheetView(sheet: sheet, desk: desk)
            }
            .task(id: app.stage) {
                if app.stage == .open { desk.attach(app) }
            }
    }
}

private struct DeskSheetView: View {
    let sheet: Desk.Sheet
    let desk: Desk

    var body: some View {
        switch sheet {
        case .ask:
            AskSheet { question in Task { await desk.ask(question) } }
        case .plan:
            PlanSheet(model: desk.plan, showSearch: desk.showSearch)
                .onChange(of: desk.plan.doing == nil) { _, done in if done { desk.sheet = nil } }
                .onDisappear { if desk.plan.doing != nil { desk.plan.stop() } }
        case .review:
            ReviewSheet(model: desk.review, open: desk.open)
        case .commitments:
            CommitmentsSheet(tasks: desk.tasks, brief: desk.brief, attention: desk.attention, open: desk.open)
                .task { if !desk.brief.opened { await desk.brief.refresh() } }
        case .curate:
            CurateSheet(model: desk.curate, nameOf: desk.nameOf) { account in
                desk.go(.folder(account: account, folder: "INBOX"))
            }
        case .filter(let form):
            FilterSheet(form: form, model: desk.curate)
        case .snoozeUntil(let copies, let canUnsnooze):
            SnoozeUntilSheet(copies: copies, model: desk.attention, canUnsnooze: canUnsnooze)
        }
    }
}

/// The cards under a message's or a conversation's subject (the reader's `.top` slot): its summary and what it
/// proposes. The reader's toolbar gains the verbs on the message from here too.
struct DeskTopCards: View {
    let context: ReaderContext
    @Environment(Desk.self) private var desk: Desk?

    var body: some View {
        if let desk {
            let copy = context.copy
            // The summary ingest stored on the message's row, when the row is this message's.
            let stored = context.row.flatMap { MailCopy($0) == copy ? (summary: $0.summary, action: $0.action) : nil }
            VStack(spacing: 8) {
                SummaryCard(copy: copy, stored: stored, cards: desk.summaries)
                SuggestionCard(copy: copy, cards: desk.suggestions, notices: desk.notices)
            }
            .toolbar {
                ToolbarItemGroup {
                    ReaderTools(copy: copy, desk: desk)
                }
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
                       link: desk.link, notices: desk.notices)
        }
    }
}

/// The reader's toolbar items for the message open: summarize, find dates, snooze, Reply Later and mute.
private struct ReaderTools: View {
    let copy: MailCopy
    let desk: Desk

    var body: some View {
        Button("Summarize", systemImage: "sparkles") { Task { await desk.summaries.summarize(copy) } }
            .help("AI summary")
        Button("Find Events and Tasks", systemImage: "calendar.badge.plus") { Task { await desk.suggestions.find(in: copy) } }
            .help("Find events and tasks in this message")
        SnoozeMenu(copies: [copy], stack: desk.stack, model: desk.attention) { desk.askWhen($0) }
            .help("Snooze")
        let later = desk.stack == .replyLater
        Button(later ? "Done" : "Reply Later", systemImage: later ? "checkmark" : "bookmark") {
            Task {
                if later { await desk.attention.replyLaterDone([copy]) } else { await desk.attention.replyLater([copy]) }
            }
        }
        .help(later ? "Done — remove from Reply later" : "Reply later")
        let muted = desk.stack == .muted
        Button(muted ? "Unmute" : "Mute", systemImage: muted ? "bell" : "bell.slash") {
            Task {
                if muted { await desk.attention.unmute([copy]) } else { await desk.attention.mute([copy]) }
            }
        }
        .help(muted ? "Unmute thread" : "Mute thread")
        if desk.stack == .waiting {
            Button("Dismiss Follow-Up", systemImage: "checkmark.circle") {
                Task { await desk.attention.dismissFollowup([copy]) }
            }
            .help("Dismiss follow-up")
        }
    }
}

/// A desk place, where the list stands: the brief, an answer, a Screener.
struct DeskScreen: View {
    let place: DeskPlace
    @Environment(Desk.self) private var desk: Desk?

    var body: some View {
        if let desk {
            switch place {
            case .brief:
                BriefScreen(model: desk.brief, attention: desk.attention, actions: BriefActions(
                    open: desk.open, goToStack: desk.goToStack, commitments: { desk.sheet = .commitments },
                    week: { desk.sheet = .review }, reply: desk.reply, askUntil: { desk.askWhen($0) }))
                    .task { await desk.brief.load() }
            case .ask:
                AskScreen(model: desk.asking, reading: desk.chosen, open: desk.open) { desk.sheet = .ask }
            case .screener(let account):
                ScreenerScreen(model: desk.screener(for: account))
            }
        }
    }
}

/// The desk's rows in the sidebar: today's brief, and each account's Screener while senders wait in it.
struct DeskSidebar: View {
    @Environment(Desk.self) private var desk: Desk?

    var body: some View {
        if let desk {
            Label("Today’s Brief", systemImage: "sun.max")
                .tag(Place.desk(.brief))
            ForEach(desk.accounts.filter { (desk.screeners[$0]?.waiting ?? 0) > 0 }, id: \.self) { account in
                Label(desk.accounts.count > 1 ? "Screener · \(desk.nameOf(account))" : String(localized: "Screener"),
                      systemImage: "person.badge.shield.checkmark")
                    .badge(desk.screeners[account]?.waiting ?? 0)
                    .tag(Place.desk(.screener(account: account)))
            }
        }
    }
}

/// The attention verbs and the AI's on the messages chosen, in the mailbox's Message menu.
struct DeskMessageItems: View {
    @FocusedValue(\.mailDesk) private var desk: Desk?

    var body: some View {
        let copies = desk?.chosenCopies ?? []
        if let desk, !copies.isEmpty {
            AttentionMenuItems(copies: copies, stack: desk.stack, model: desk.attention,
                               askUntil: { desk.askWhen($0) }, shortcuts: true)
            if copies.count == 1, let copy = copies.first {
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

/// The menu bar's Assistant menu: the assistant's and curation's panels.
struct DeskCommands: Commands {
    @FocusedValue(\.mailDesk) private var desk: Desk?

    var body: some Commands {
        CommandMenu("Assistant") {
            Button("Today’s Brief", systemImage: "sun.max") { desk?.go(.desk(.brief)) }
                .keyboardShortcut("b", modifiers: [.option, .command])
                .disabled(desk == nil)
            Button("Ask Your Mail…", systemImage: "sparkles") { desk?.sheet = .ask }
                .keyboardShortcut("a", modifiers: [.option, .command])
                .disabled(desk == nil)
            // ⌥⌘D hides the Dock everywhere, so Bulk Action takes ⌥⌘K, beside the palette's ⌘K.
            Button("Bulk Action…", systemImage: "wand.and.stars") { desk?.startPlan() }
                .keyboardShortcut("k", modifiers: [.option, .command])
                .disabled(desk == nil)
            Divider()
            Button("Commitments and Tasks", systemImage: "checklist") { desk?.sheet = .commitments }
                .disabled(desk == nil)
            Button("Your Week", systemImage: "calendar") { desk?.sheet = .review }
                .disabled(desk == nil)
            Divider()
            Button("Curate — Rules and Senders…", systemImage: "slider.horizontal.3") { desk?.sheet = .curate }
                .disabled(desk == nil)
            Button("New Filter…", systemImage: "line.3.horizontal.decrease.circle") { desk?.newFilter() }
                .disabled(desk == nil)
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

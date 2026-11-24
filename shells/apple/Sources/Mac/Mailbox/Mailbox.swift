#if os(macOS)
import AppKit
import Observation
import SwiftUI

/// The mailbox as the window sees it, as the page's useMailbox.ts holds it: the sidebar's accounts, folders, views,
/// labels and counts, the preferences the list reads, the Inbox's tab, the rows chosen, and what a person was last told.
/// Its verbs (Verbs.swift) and its ways about (Going.swift) go through the routes the page uses. It decides nothing
/// about mail: which rows a list holds and in what order are the server's.
@MainActor
@Observable
final class Mailbox {
    @ObservationIgnored weak var app: MailApp?
    /// The routes, when they aren't the app's core's: a test's.
    @ObservationIgnored var wire: Wire?
    /// The window's, where Edit ▸ Undo reaches what the mailbox did.
    @ObservationIgnored weak var undoManager: UndoManager?

    // The sidebar, as /api/digest, /api/folders, /api/folders/across, /api/labels and /api/views say.
    private(set) var accounts: [String] = []
    private(set) var aliases: [String: String] = [:]
    private(set) var folders: [String: API.Folders] = [:]
    private(set) var labels: [String: [API.Label]] = [:]
    private(set) var across: API.ViewCounts?
    var views: [API.ListedView] = []
    /// Whether the AI is on, which the contact card's summary needs.
    private(set) var aiOn = false
    /// How AI stands, as the digest last said: what is off and why, so what only AI serves can go once it is turned
    /// off.
    private(set) var ai: API.AiStatus?
    /// AI was turned off in Settings: what only AI serves isn't offered, and nothing asks to turn it on.
    var aiTurnedOff: Bool { ai?.isTurnedOff == true }
    /// The AI is reading mail that waits for its summary, so an emptied Needs you says so rather than that nothing
    /// needs you.
    var summarizing: Bool { ai?.isSummarizing == true }

    // The preferences the list reads (/api/preferences), as the server holds them.
    private(set) var grouped = true
    private(set) var scope = API.SearchScope.all
    private(set) var singleKeys = true

    /// The Inbox's tab. Changing it lists the tab, and reads the brief's tallies with it: the brief's line over the
    /// list counts what needs you too, so the two count the same mail.
    var tab = InboxTab.needs {
        didSet {
            guard tab != oldValue else { return }
            app?.openList()
            rereadsTallies?()
        }
    }
    /// What the list's server said besides its rows.
    private(set) var facts = ListFacts()
    /// A lane's search, or the Document Hub, while the window shows one in place of rows.
    private(set) var lane: LaneModel?
    private(set) var documents: DocumentsModel?
    /// The rows a person chose, as they were when chosen, in the list's order: one is open in the reader, several are
    /// marked. Kept whole, so a verb reaches rows scrolled far from the screen, whose pages the list has let go.
    var chosenRows: [API.FolderRow] = []
    var marked: [MailCopy] { chosenRows.map(MailCopy.init) }
    /// One row marked on its own (x, or its check box): the row chosen is a mark, as several are, not the one open.
    var markedAlone = false
    /// Rows are marked: several chosen, or one marked on its own. The verbs and the bulk bar act on the marks, and the
    /// message open, if any, is only the row the keyboard is on, which j and k move without losing them.
    var marking: Bool {
        chosenRows.count + unheld.count > 1 || (markedAlone && !(chosenRows.isEmpty && unheld.isEmpty))
    }
    /// Whether a verb has anything to act on.
    var hasTarget: Bool { !chosenRows.isEmpty || !unheld.isEmpty || app?.selection.copy != nil }
    /// Positions chosen whose rows weren't held when chosen (Select All in a long list): read before a verb acts.
    @ObservationIgnored private(set) var unheld = IndexSet()
    /// The row of the message open when neither the list nor a lane holds it (one opened from the brief, an answer,
    /// the gate, the Document Hub, a sender's card or a notification), as the server last said it stands, and as its
    /// folder lists it once that has been read (`shown`).
    private(set) var outside: API.FolderRow?
    /// Each read of how the message open from outside stands, counted, so one answered late never overwrites a later.
    @ObservationIgnored private var outsideReads = 0
    /// The message open from outside as its folder lists it, and the message whose listing was last asked for: read
    /// once as it opens (`readListing`).
    @ObservationIgnored private var outsideListed: API.FolderRow?
    @ObservationIgnored private var listing: MailCopy?

    /// What a person was last told, with the Undo it offers.
    private(set) var toast: Toast?
    /// The Move To sheet, while it is open.
    var moving: MoveAsk?
    /// The folder dialog, while it is open (`FolderSheet`).
    var folderAsk: FolderAsk?
    /// The command palette, while it is open.
    var paletteOpen = false
    /// A search being saved as a view: its query, while the sheet asks its name.
    var savingView: String?
    /// The sender whose card is open.
    var contact: String?
    /// Whether the search field has the focus: `/` and Search (⌘F) put it there.
    var searchFocus = false
    /// The search field has the keyboard, as Return leaves it: the empty pane says ↓ in place of J.
    var inSearchField = false
    /// Where the person was when they asked their mail a question: Esc on the answer goes back there (`leaveAsk`).
    var askedFrom: Place?
    /// A text of the window's that can be edited has the keys (the search, the reader's reply box, Find, a dialog's
    /// field), as the window's first responder says (`TypingWatch`).
    var editingText = false
    /// A dialog of the window's is up, whose keys are its own.
    var dialogUp = false
    /// The list's keys, the frame's Esc and the Message menu's ⌘⌫ wait: text is being typed, where ⌘⌫ deletes text
    /// and not mail, or a dialog is up.
    var typing: Bool { editingText || dialogUp }
    /// The rows being dragged from the list, which the rail's folders take (`drop(on:of:)`); none between drags.
    var dragging: [API.FolderRow] = []

    @ObservationIgnored private var gathered: Gathered?
    @ObservationIgnored private var load: MailListModel.Loader?
    @ObservationIgnored private var listNumber = 0
    @ObservationIgnored private var toastNumber = 0
    @ObservationIgnored private var digestSoon: Task<Void, Never>?
    /// What the mailbox last put on Edit ▸ Undo: while it is the next thing undone there, Undo goes through it.
    @ObservationIgnored var offered: String?
    /// The message the last thing that can be undone moved the pane on from, which its Undo opens again
    /// (`undoNow`), and whether what was said last offered an Undo, so a verb that can't be undone never sets it.
    @ObservationIgnored var reopens: MailCopy?
    @ObservationIgnored var reopenable = false
    /// The messages open in windows of their own, each of which closes once a verb from any window sends its message
    /// away (`sentAway`); and the message of the one the last thing that can be undone closed, which its Undo opens
    /// in a window of its own again.
    @ObservationIgnored private var ownWindows: [OwnWindow] = []
    @ObservationIgnored var reopensWindow: MailCopy?
    /// The assistant's (area D): what `?words` in the search field asks, and what `!words` plans.
    @ObservationIgnored var asks: ((String) -> Void)?
    @ObservationIgnored var plans: ((String) -> Void)?
    /// The composer's (area B): a new message to an address, which the contact card offers.
    @ObservationIgnored var composeTo: ((String) -> Void)?
    /// The other areas' commands, which the palette lists with the mailbox's own.
    @ObservationIgnored var more: [@MainActor (Mailbox) -> [MailCommand]] = []
    /// The other areas' single keys (r to reply, h to snooze, ? for help), which the list runs while it has the focus.
    @ObservationIgnored var keys: [Character: @MainActor () -> Void] = [:]
    /// The reader's: a message in a window of its own, which a double-click on its row opens.
    @ObservationIgnored var opensWindow: ((MailCopy) -> Void)?
    /// The composer's: what a draft's row says in its time's place, when it waits to go later ("Scheduled · …").
    @ObservationIgnored var draftWhen: (@MainActor (API.FolderRow) -> String?)?
    /// The other areas' items in a row's context menu, for the rows it acts on.
    @ObservationIgnored var rowMenu: [@MainActor ([API.FolderRow]) -> [NSMenuItem]] = []
    /// The desk's: today's brief in the pane, opened on purpose (a click, Return) or only arrived at (the arrow
    /// keys onto its row), which writes none.
    @ObservationIgnored var opensBrief: (@MainActor (_ deliberately: Bool) -> Void)?
    /// The desk's: ask when to bring these rows back, for a row's snooze button and the bulk bar's.
    @ObservationIgnored var snoozes: (@MainActor ([API.FolderRow]) -> Void)?
    /// The assistant's: the search a sentence typed in the field means, or nil to leave it as typed; and a sentence
    /// to leave as typed from now on (the translated chip's ×).
    @ObservationIgnored var translates: (@MainActor (String) async -> String?)?
    @ObservationIgnored var keepsLiteral: ((String) -> Void)?
    /// The sentence being turned into a search now, if one is; and the search left before its translation came.
    @ObservationIgnored var translating: (@MainActor () -> String?)?
    @ObservationIgnored var stopsTranslating: (() -> Void)?
    /// The desk's: an Undo is asked, and what it does once answered with what it put back, or nil when it failed, so
    /// mail an Undo puts back in an Inbox is never said to be new (`SinceLooked.undone`).
    @ObservationIgnored var undoing: (@MainActor () -> @MainActor (API.Undone?) -> Void)?
    /// The desk's: mail a verb sent away from where it was, so the gate whose card shows it reads its queue again.
    @ObservationIgnored var wentAway: (@MainActor ([MailCopy]) -> Void)?
    /// The desk's: read the brief's tallies again, which the rail and the brief's line over the Inbox count.
    @ObservationIgnored var rereadsTallies: (@MainActor () -> Void)?
    /// The desk's: a message's own window inside the mail window's desk, so its reader shows the cards the pane's
    /// does (the summary and what the message proposes, an invite with its RSVP).
    @ObservationIgnored var inDesk: (@MainActor (AnyView) -> AnyView)?
    /// The desk's: do now what the menus asked of the mail window (`Desk.asked`) when they were drawn with none open
    /// and one has opened since.
    @ObservationIgnored var doesAsked: (@MainActor () -> Void)?
    /// The areas that have hung their hooks here, each once a window however often its view appears.
    @ObservationIgnored private var joined: Set<String> = []
    /// Counted up each time the hooks are let go (`leave`): an area's view that is still on screen joins again when
    /// it changes.
    private(set) var joinRound = 0

    init(app: MailApp? = nil) {
        self.app = app
    }

    /// Let an area hang its hooks (`asks`, `keys`, `more`, `rowMenu` and the rest) on the mailbox, once a window.
    func join(_ area: String, _ hooks: (Mailbox) -> Void) {
        guard joined.insert(area).inserted else { return }
        hooks(self)
    }

    /// Let go of every area's hooks, as the window whose views hung them closes: they reach that window's desk, shell
    /// and pane, which go with it. The process's one mailbox outlives its windows, so the window opened again from
    /// the Dock hangs its own, and the areas of a window still open hang theirs again (`joinRound`).
    func leave() {
        joined = []
        asks = nil
        plans = nil
        composeTo = nil
        opensWindow = nil
        draftWhen = nil
        more = []
        keys = [:]
        rowMenu = []
        opensBrief = nil
        snoozes = nil
        translates = nil
        keepsLiteral = nil
        translating = nil
        stopsTranslating = nil
        undoing = nil
        wentAway = nil
        rereadsTallies = nil
        inDesk = nil
        doesAsked = nil
        joinRound += 1
    }

    /// The routes: the test's, else the app's core's once it is open.
    var calls: Wire? {
        wire ?? app?.core.map(Wire.init)
    }

    /// An account's name as the rail shows it: its alias, else its address's local part.
    func name(of account: String) -> String {
        aliases[account].flatMap { $0.isEmpty ? nil : $0 } ?? Words.short(account)
    }

    /// The place's account's folders, as /api/folders listed them.
    func folders(of account: String?) -> [API.RailFolder] {
        account.flatMap { folders[$0]?.folders } ?? []
    }

    /// Whether `copy` is in its account's Trash, where the pane's bar restores it.
    func inTrash(_ copy: MailCopy) -> Bool {
        folders(of: copy.account).first { $0.name == copy.folder }?.role == "trash"
    }

    /// Every account's Inbox unread together, as the rail counts the Inbox; none until each account's folders are read
    /// with its Inbox among them, so nothing says a count that is still coming.
    var inboxesUnread: Int? {
        guard !accounts.isEmpty else { return nil }
        var unread = 0
        for account in accounts {
            guard let inbox = folders(of: account).first(where: { $0.role == "inbox" }) else { return nil }
            unread += inbox.unseen
        }
        return unread
    }

    /// The list's title (`title` in useMailbox.ts).
    var title: String {
        guard let place = app?.place else { return "" }
        return place.title(folders: folders(of: place.account))
    }

    // MARK: Reading the sidebar

    /// Read everything the sidebar shows: the accounts and their names, each one's folders and labels, the counts over
    /// every account, the views, and the preferences.
    func refresh() async {
        await readDigest()
        await readCounts()
        await readLabels()
        await readViews()
        await readPreferences()
    }

    /// The accounts, their names, and whether this run files.
    func readDigest() async {
        guard let calls, let digest = try? await calls.get(API.Get.Digest.self) else { return }
        accounts = digest.accounts
        aliases = digest.aliases.compactMapValues { $0 }
        aiOn = digest.ai.isOn
        ai = digest.ai
    }

    /// Each account's folders and its virtual folders' counts, and those over every account.
    func readCounts() async {
        guard let calls else { return }
        for account in accounts {
            if let read = try? await calls.get(API.Get.Folders.self, ["account": account]) {
                folders[account] = read
            }
        }
        across = try? await calls.get(API.Get.FoldersAcross.self)
    }

    /// A Gmail account's own labels. Any other account has none.
    func readLabels() async {
        guard let calls else { return }
        for account in accounts {
            labels[account] = (try? await calls.get(API.Get.Labels.self, ["account": account]))?.labels ?? []
        }
    }

    func readViews() async {
        guard let calls, let read = try? await calls.get(API.Get.Views.self) else { return }
        views = read.views
    }

    /// What the list reads of a person's settings. A change in Settings is read here when the list next opens.
    func readPreferences() async {
        guard let calls, let read = try? await calls.get(API.Get.Preferences.self) else { return }
        let was = (grouped, scope)
        grouped = read.preferences.conversations
        scope = read.preferences.searchScope
        singleKeys = read.preferences.singleKeys
        if was != (grouped, scope) { app?.openList() }
    }

    /// What the event stream said changed, as the page's realtime.ts reads it again: the counts after mail changed,
    /// the accounts' names a moment later, and the labels and views after a sync. An event of an account the rail
    /// doesn't list yet (one just added) reads the accounts first, so its folders are read with the rest; and when the
    /// accounts the digest names have changed, their folders and counts are read again. Summaries landing change what
    /// rows say and which need you, and no folder's count: a lane on screen and the message open from outside are read
    /// again, the list by the window (`MailApp.heard`) and the rail's count with the brief's (the desk's `heard`), and
    /// the digest, whose AI says whether mail still waits to be read (`summarizing`).
    func heard(_ event: CoreEvent) {
        if event.name == MailApp.summarized {
            lane?.refresh()
            rereadOutside()
            digestAgain()
        }
        if MailApp.mailChanged.contains(event.name) || event.name == "folder-synced" {
            lane?.refresh()
            rereadOutside()
            if let account = Self.account(of: event), !accounts.contains(account) {
                Task {
                    await readDigest()
                    await readCounts()
                }
            } else {
                Task { await readCounts() }
                digestAgain()
            }
        }
        if event.name == "sync-done" {
            documents?.reload()
            Task {
                await readLabels()
                await readViews()
            }
        }
    }

    /// The digest a moment after an event, read once for a burst of them. Events that come while a read waits join it
    /// rather than put it off: the older-mail lane says folder-synced at each step for minutes after a first sync, and
    /// a wait begun afresh at each would leave the rail as it was until the steps stopped.
    private func digestAgain() {
        guard digestSoon == nil else { return }
        digestSoon = Task {
            try? await Task.sleep(for: .milliseconds(400))
            digestSoon = nil
            let was = accounts
            await readDigest()
            if accounts != was {
                await readCounts()
                await readLabels()
            }
        }
    }

    /// The account an event says it is of, if it names one.
    static func account(of event: CoreEvent) -> String? {
        guard case .string(let account)? = event.decoded([String: JSONValue].self)?["account"], !account.isEmpty
        else { return nil }
        return account
    }

    // MARK: The list

    /// How the window reads the list at `place`, or nil for a place that isn't a list of mail (the Document Hub, a
    /// lane, which show their own). Every list reads 200 rows at a time; an Inbox's Needs you and Unread, and a
    /// search, read 200 and 200 more when asked, as the page's list does.
    func loader(for place: Place) -> MailListModel.Loader? {
        listNumber += 1
        facts = ListFacts()
        gathered = nil
        load = nil
        lane = nil
        documents = nil
        guard let calls else { return nil }
        let number = listNumber
        let report: @Sendable (ListFacts) async -> Void = { [weak self] facts in
            await self?.listSaid(facts, number)
        }
        let grouped = grouped, scope = scope, tab = tab
        let read: Gathered.Read
        switch place {
        case .allInboxes:
            read = { offset, limit in try await Lists.inboxes(calls, grouped: grouped, offset: offset, limit: limit) }
        case .folder(let account, let folder):
            read = { offset, limit in
                try await Lists.folder(calls, account: account, folder: folder, grouped: grouped, offset: offset, limit: limit)
            }
        case .virtual(let virtual, let account):
            read = { offset, limit in
                try await Lists.folder(calls, account: account, folder: virtual.rawValue, grouped: grouped,
                                       offset: offset, limit: limit)
            }
        case .search(let search):
            if search.lane != nil {
                let lane = LaneModel(search: search, scope: scope, wire: calls)
                self.lane = lane
                lane.refresh()
                return nil
            }
            read = { offset, limit in try await Lists.search(calls, search, scope: scope, offset: offset, limit: limit) }
            // The row open stays until a person leaves it, as the Inbox's tabs keep it.
            let gathered = Gathered(read: read, opened: { [weak self] in await self?.openCopy() },
                                    standing: { copy in
                                        await Lists.row(calls, account: copy.account, folder: copy.folder, uid: copy.uid,
                                                        key: copy.key)
                                    }, report: report)
            self.gathered = gathered
            load = gathered.load
            return gathered.load
        case .documents:
            let documents = DocumentsModel(wire: calls)
            self.documents = documents
            documents.reload()
            return nil
        case .desk:
            // The desk's screens read what they show themselves.
            return nil
        }
        if place.isInbox && tab != .all {
            let gathered = Gathered(read: read, keep: { tab.keeps($0, opened: $1) },
                                    opened: { [weak self] in await self?.openCopy() }, report: report)
            self.gathered = gathered
            load = gathered.load
        } else {
            load = Lists.paged(read, report: report)
        }
        return load
    }

    private func openCopy() -> MailCopy? {
        app?.selection.copy
    }

    private func listSaid(_ facts: ListFacts, _ number: Int) {
        guard number == listNumber else { return }
        self.facts = facts
    }

    /// Read 200 more of a search or an Inbox's tab (the page's "Load 200 more").
    func readMore() {
        guard let gathered else { return }
        Task {
            await gathered.readMore()
            app?.list?.refresh()
        }
    }

    /// Group the lists into conversations, or not, as the server keeps the preference.
    func setGrouped(_ on: Bool) {
        grouped = on
        app?.openList()
        Task { await savePreference(API.ChangedPreferences(conversations: on)) }
    }

    /// Search every account, or the one the search was asked from.
    func setScope(_ scope: API.SearchScope) {
        self.scope = scope
        app?.openList()
        Task { await savePreference(API.ChangedPreferences(searchScope: scope)) }
    }

    private func savePreference(_ changes: API.ChangedPreferences) async {
        guard let calls else { return }
        do {
            _ = try await calls.post(API.Post.Preferences.self, changes)
        } catch {
            say(String(localized: "Couldn’t save that setting"), detail: (error as? Problem)?.detail)
            await readPreferences()
        }
    }

    // MARK: The rows chosen

    /// The table's choice changed: one row opens in the reader, several are marked and none is open.
    func chose(_ rows: [API.FolderRow], unheld: IndexSet = []) {
        // A row chosen beside the brief goes back to the Inbox, whose list it is in.
        if !rows.isEmpty || !unheld.isEmpty, let app, let listed = app.listed, app.place != listed {
            app.place = listed
        }
        chosenRows = rows
        self.unheld = unheld
        markedAlone = false
        let one = rows.count == 1 && unheld.isEmpty ? MailCopy(rows[0]) : nil
        app?.open(one)
        if let one { warmNext(after: one) }
    }

    /// Open `row` beside the rows marked, which stay marked: the row the keyboard is on, as j and k move it.
    func openBeside(_ row: API.FolderRow) {
        let copy = MailCopy(row)
        app?.open(copy)
        warmNext(after: copy)
    }

    /// x, or a row's check box: mark `row`, or take its mark off (`toggleMark`). The message open stays open: x on its
    /// row marks it alone, a check box beside it marks both, and once only the open message's row is left, or none,
    /// that row is chosen alone again.
    func toggleMark(_ row: API.FolderRow) {
        let copy = MailCopy(row)
        var rows = chosenRows
        if marking {
            if let at = rows.firstIndex(where: { MailCopy($0) == copy }) { rows.remove(at: at) } else { rows.append(row) }
        } else if rows.isEmpty || rows.map(MailCopy.init) == [copy] {
            rows = [row]
        } else {
            rows.append(row)
        }
        let open = app?.selection.copy
        let removed = rows.count < chosenRows.count
        if unheld.isEmpty, rows.isEmpty || (removed && rows.map(MailCopy.init) == [open]) {
            markedAlone = false
            chosenRows = open.flatMap(self.row(of:)).map { [$0] } ?? []
            return
        }
        // In the list's order, as rows chosen together are.
        if let list = app?.list {
            let at = rows.map { list.position(of: MailCopy($0)) ?? Int.max }
            rows = rows.indices.sorted { (at[$0], $0) < (at[$1], $1) }.map { rows[$0] }
        }
        chosenRows = rows
        markedAlone = true
    }

    /// x: mark the row the keyboard is on, the message open in the list, or take its mark off. With no row of the
    /// list open, it says so.
    func markOpen() {
        guard let open = app?.selection.copy, let row = row(of: open) else { return saysNothingChosen() }
        toggleMark(row)
    }

    /// Show today's brief in the pane beside the list: opened on purpose, or arrived at by the arrow keys.
    func openBrief(deliberately: Bool) {
        if let opensBrief { opensBrief(deliberately) } else { app?.place = .desk(.brief) }
    }

    /// Mark no rows but the one open.
    func clearMarks() {
        chose(app?.selection.copy.flatMap(row(of:)).map { [$0] } ?? [])
    }

    /// Open a message from outside the list: the brief's cards, an answer's mail, the gate, the Document Hub, a
    /// sender's card, a sheet's message. Where the list on screen holds its row, the row is chosen, as a click on it
    /// would; anywhere else no row stays chosen, so the verbs act on this message and not on a row chosen before.
    func open(_ copy: MailCopy) {
        app?.openings += 1
        if let app, app.place == app.listed, let row = row(of: copy) {
            chose([row])
            return
        }
        chosenRows = []
        unheld = []
        markedAlone = false
        app?.open(copy)
        // How it stands is read as the reader opens it, so the menus' Star and Mark as Read say what they will do.
        Task { _ = await readOutside(copy) }
    }

    // MARK: Windows of their own

    /// A message's own window, held weakly: it goes when the window does.
    private struct OwnWindow {
        weak var verbs: MessageWindowVerbs?
    }

    /// A message opened in a window of its own, whose window closes once its message is sent away from any window.
    func opened(window verbs: MessageWindowVerbs) {
        ownWindows.removeAll { $0.verbs == nil || $0.verbs === verbs }
        ownWindows.append(OwnWindow(verbs: verbs))
    }

    /// `copies` were sent away from where they were (filed, deleted, junked, moved, snoozed, done with), by a verb
    /// from any window: the windows of their own that show one close, and the last that can be undone opens again
    /// on its Undo, as the pane's message does. The gate showing one reads its queue again.
    func sentAway(_ copies: [MailCopy]) {
        wentAway?(copies)
        let gone = Set(copies)
        ownWindows.removeAll { $0.verbs == nil }
        for window in ownWindows.compactMap(\.verbs) where gone.contains(window.copy) && !window.left {
            window.sentAway()
            if reopenable { reopensWindow = window.copy }
        }
    }

    /// Open a message where a notification named it: its folder's list, and the message in it, chosen in its row as a
    /// click chooses it once the list holds it. Its folder's rows come after it opens, so the row is chosen as they
    /// come, unless another message was opened or a row chosen meanwhile.
    func openInItsFolder(_ copy: MailCopy) {
        app?.place = .folder(account: copy.account, folder: copy.folder)
        open(copy)
        guard let app, chosenRows.isEmpty, let list = app.list else { return }
        let turn = app.openings
        let before = list.onceRead
        list.onceRead = { [weak self, weak app, weak list] in
            before?()
            guard let self, let app, let list, app.list === list, app.openings == turn, app.selection.copy == copy,
                  self.chosenRows.isEmpty, let row = self.row(of: copy) else { return }
            self.chose([row])
        }
    }

    /// The rows a verb acts on: those marked, else the message open, as the list, a lane or the server has its row now.
    /// Rows marked that weren't held are read first. A verb that names the message by its copy alone (File, Move,
    /// Snooze) passes `standing: false`, and never waits on the reader or the server to say how it stands.
    func targets(standing: Bool = true) async -> [API.FolderRow] {
        guard marking || !unheld.isEmpty else {
            if let open = app?.selection.copy { return [standing ? await openRow(open) : knownRow(open)] }
            return chosenRows
        }
        var rows = chosenRows
        guard let load, let list = app?.list else { return rows }
        let pages = Set(unheld.map { $0 / MailListModel.pageSize })
        var read: [Int: API.FolderRow] = [:]
        for page in pages.sorted() {
            guard let sheet = try? await load(page * MailListModel.pageSize, MailListModel.pageSize) else { continue }
            for (index, row) in sheet.rows.enumerated() { read[page * MailListModel.pageSize + index] = row }
        }
        let known = Set(rows.map(MailCopy.init))
        for position in unheld {
            if let row = read[position] ?? list.copy(at: position), !known.contains(MailCopy(row)) { rows.append(row) }
        }
        return rows
    }

    /// What the pane's bar and the reader's own buttons act on: the message in the pane alone, whatever rows are
    /// marked, as the page's reader files the message open (`fileOpen`). The list's keys, the bulk bar and the
    /// Message menu act on the marks (`targets`).
    func paneTargets(standing: Bool = true) async -> [API.FolderRow] {
        guard let open = app?.selection.copy else { return [] }
        return [standing ? await openRow(open) : knownRow(open)]
    }

    /// The row the list holds for a copy.
    func row(of copy: MailCopy) -> API.FolderRow? {
        guard let list = app?.list, let position = list.position(of: copy) else { return nil }
        return list.copy(at: position)
    }

    /// The row the window holds for a copy as it stands now: the list's, a lane's, else the one chosen as it was when
    /// chosen. A verb's mark and the list read again since change the list's row, never the one kept when chosen.
    func heldRow(of copy: MailCopy) -> API.FolderRow? {
        if let row = row(of: copy) { return row }
        if let hit = lane?.hits.first(where: { MailCopy(API.FolderRow($0)) == copy }) { return API.FolderRow(hit) }
        return chosenRows.first { MailCopy($0) == copy }
    }

    /// `rows` as the window holds them now, for one row; several marked are acted on as they were chosen, by verbs
    /// that say what they do whatever each row's flags.
    func current(_ rows: [API.FolderRow]) -> [API.FolderRow] {
        guard rows.count == 1 else { return rows }
        return [heldRow(of: MailCopy(rows[0])) ?? rows[0]]
    }

    /// What a verb would act on, as the window knows it without asking, for a menu's words: the rows marked, else the
    /// message open.
    var shownRows: [API.FolderRow] {
        guard !marking, unheld.isEmpty, let open = app?.selection.copy else { return chosenRows }
        return [knownRow(open)]
    }

    /// The open message's row as the window knows it without asking: the one it holds, else as the server last said
    /// it stands, else its copy and what the reader says of it.
    private func knownRow(_ open: MailCopy) -> API.FolderRow {
        shownRow(of: open) ?? API.FolderRow(open, head: app?.reader.opened(open), member: nil)
    }

    /// The row the reader shows `copy` by, for its time, its chips and its summary: the one the window holds, else, for
    /// a message opened from outside the list, as the server last said it stands. None while that is still being read.
    func shownRow(of copy: MailCopy) -> API.FolderRow? {
        heldRow(of: copy) ?? outside.flatMap { MailCopy($0) == copy ? $0 : nil }
    }

    /// The open message's row: the one the window holds, else what the server says of it.
    func openRow(_ open: MailCopy) async -> API.FolderRow {
        if let held = heldRow(of: open) { return held }
        return await readOutside(open)
    }

    /// Whether `copy`, the message open, is unread: as the window holds its row, else as the server says it stands.
    /// One opened from the brief, an answer or the gate is marked read as it is read, as one opened from the list is.
    func opensUnread(_ copy: MailCopy) async -> Bool {
        guard app?.selection.copy == copy else { return false }
        return await !openRow(copy).seen
    }

    /// The message open was read for its moment (the reader's dwell): marked read at the server where the profile lets
    /// opening mark it, if it is unread, and shown read at once (`markedRead`).
    func markOpenedRead(_ copy: MailCopy, profile: String?) async {
        guard let calls, await opensUnread(copy) else { return }
        guard app?.selection.copy == copy, await MarksOpened.mark(copy, routes: calls, profile: profile) else { return }
        markedRead(copy)
    }

    /// `copy` was marked read on opening: its row says so now wherever the window holds it (the list's, a lane's, the
    /// one chosen, the message open from outside), so its dot, its weight and the Message menu's words change at once;
    /// then the lists and the counts are read again, as after a verb's mark. A row that stands for a whole
    /// conversation is left to the server, which says whether any of it is still unread.
    func markedRead(_ copy: MailCopy) {
        let grouped = grouped
        app?.list?.patch(copy) { if !$0.isConversation(grouped: grouped) { $0.seen = true } }
        lane?.patch(copy) { $0.seen = true }
        for at in chosenRows.indices
        where MailCopy(chosenRows[at]) == copy && !chosenRows[at].isConversation(grouped: grouped) {
            chosenRows[at].seen = true
        }
        if let open = outside, MailCopy(open) == copy { outside?.seen = true }
        readAgain()
    }

    /// A message neither the list nor a lane holds, as the server says it stands (`standing`), its head as the reader
    /// opened it: once the reader has, when it is still opening it. It stands for itself alone, as the pane shows it.
    /// The last read asked is the one kept, with what its folder's listing said of it (`readListing`).
    private func readOutside(_ copy: MailCopy) async -> API.FolderRow {
        outsideReads += 1
        let read = outsideReads
        let (row, listed) = await standingRead(of: copy, head: await app?.reader.head(of: copy))
        guard app?.selection.copy == copy, read == outsideReads else { return row }
        if listed { outsideListed = row }
        outside = Self.shown(row, listed: outsideListed)
        if !listed { readListing(copy) }
        return row
    }

    /// The row the reader shows for a message opened from outside the list: its listing's, for its summary, its next
    /// step, whether it needs you and when it came, with its read and starred marks as the server last said them.
    static func shown(_ standing: API.FolderRow, listed: API.FolderRow?) -> API.FolderRow {
        guard var listed, MailCopy(listed) == MailCopy(standing) else { return standing }
        listed.seen = standing.seen
        listed.flagged = standing.flagged
        return listed
    }

    /// What only a folder's listing says of a message (the summary ingest stored, its next step, whether it needs you,
    /// when it came, its bucket), read once as it opens from outside the list, apart from how it stands: a verb that
    /// waits on how it stands never waits on its folder being read.
    private func readListing(_ copy: MailCopy) {
        guard let calls, listing != copy else { return }
        listing = copy
        Task {
            guard let listed = await Lists.row(calls, account: copy.account, folder: copy.folder, uid: copy.uid,
                                               key: copy.key),
                  listing == copy else { return }
            outsideListed = listed
            if let now = outside, MailCopy(now) == copy, app?.selection.copy == copy {
                outside = Self.shown(now, listed: listed)
            }
        }
    }

    /// A message as the server says it stands, for itself alone: read and starred as its conversation's member says,
    /// the conversation named by its head; else, its conversation not known or the message not in it, as its folder
    /// lists it. Neither found, it is named by its copy and its head alone, read and not starred.
    func standing(of copy: MailCopy, head: API.MessageOpened?) async -> API.FolderRow {
        await standingRead(of: copy, head: head).row
    }

    /// `standing`, and whether it is the folder's listing that said so.
    private func standingRead(of copy: MailCopy, head: API.MessageOpened?) async -> (row: API.FolderRow, listed: Bool) {
        guard let calls else { return (API.FolderRow(copy, head: head, member: nil), false) }
        if let thread = head?.threadId, !thread.isEmpty,
           let items = try? await calls.get(API.Get.Thread.self, ["account": copy.account, "thread_id": thread]).items,
           // A Gmail message shows once among them, as whichever of its copies came first: the same message all the
           // same.
           let member = items.first(where: { MailCopy($0, account: copy.account) == copy })
               ?? items.first(where: { $0.key == copy.key })
        {
            return (API.FolderRow(copy, head: head, member: member), false)
        }
        if let listed = await Lists.row(calls, account: copy.account, folder: copy.folder, uid: copy.uid,
                                        key: copy.key) {
            return (listed, true)
        }
        return (API.FolderRow(copy, head: head, member: nil), false)
    }

    /// Read again how the message open from outside the list stands, after mail changed.
    func rereadOutside() {
        guard let open = app?.selection.copy, outside.map(MailCopy.init) == open, heldRow(of: open) == nil else { return }
        Task { _ = await readOutside(open) }
    }

    /// The next two rows' bodies made ready, so moving down shows them at once. The server skips one it would wait on.
    private func warmNext(after copy: MailCopy) {
        guard let list = app?.list, let calls, let at = list.position(of: copy) else { return }
        let next = (at + 1...at + 2).compactMap { list.copy(at: $0) }
        Task {
            for row in next {
                _ = await calls.send("GET", API.Get.MessageBody.path + Core.query([
                    "key": String(row.key), "folder": row.folder, "uid": String(row.uid), "prefetch": "1",
                ]), Data())
            }
        }
    }

    // MARK: Telling a person

    /// Tell a person what happened, with an Undo when `undo` names one: said for a moment, or longer with an Undo.
    /// Say `text` in the window's toast for a moment. A refusal (`refused`: read only, nothing chosen, a server's
    /// no) shows the alert, never the tick that says something was done.
    func say(_ text: String, detail: String? = nil, undo: String? = nil, refused: Bool = false) {
        // Something new to undo opens nothing again on its Undo, unless it moves the pane on next (`moveOn`).
        if undo != nil {
            reopens = nil
            reopensWindow = nil
        }
        reopenable = undo != nil
        toastNumber += 1
        let number = toastNumber
        toast = Toast(text: text, detail: detail, undo: undo, refused: refused)
        Task {
            try? await Task.sleep(for: .milliseconds(undo == nil ? Toast.saysMs : Toast.offersMs))
            if toastNumber == number { toast = nil }
        }
    }

    func dismissToast() {
        toast = nil
    }
}

/// What a person is told after a verb (lib/toast.tsx): its words, the server's reason when it refused, and the Undo
/// it offers.
struct Toast: Identifiable, Equatable {
    let id = UUID()
    var text: String
    var detail: String?
    var undo: String?
    /// Something asked was turned down, or nothing was done.
    var refused = false

    /// How long words stay, and an Undo offer.
    static let saysMs = 2600
    static let offersMs = 8000
}

/// A move being asked (`MoveAsk`): whose folders it may go to, and the one folder the rows are in, which isn't offered.
struct MoveAsk: Identifiable, Equatable {
    let account: String
    let from: String?
    /// The rows to move, when a row's menu asked for them: the row clicked, which needn't be the one chosen. Nil
    /// moves what is chosen, else what is open.
    var rows: [API.FolderRow]? = nil
    var id: String { account + "\u{0}" + (from ?? "") }
}

extension MailListModel {
    /// Where a copy is among the rows held.
    func position(of copy: MailCopy) -> Int? {
        position { MailCopy($0) == copy }
    }

    /// Where the first row held that `matches` is.
    func position(where matches: (API.FolderRow) -> Bool) -> Int? {
        var position = 0
        while position < total {
            guard let row = self.copy(at: position) else {
                position = (position / MailListModel.pageSize + 1) * MailListModel.pageSize
                continue
            }
            if matches(row) { return position }
            position += 1
        }
        return nil
    }
}

extension MailApp {
    /// Open `copy` in the reader, or nothing, as choosing its row does.
    func open(_ copy: MailCopy?) {
        guard copy != selection.copy else { return }
        selection.copy = copy
        reader.open(copy)
    }
}
#endif

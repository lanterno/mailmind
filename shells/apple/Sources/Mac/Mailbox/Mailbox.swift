#if os(macOS)
import AppKit
import Observation

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
    /// A read-only run (`MAILMIND_PROFILE=read-only`): filing waits for a full run.
    private(set) var readOnly = false
    /// Whether the AI is on, which the contact card's summary needs.
    private(set) var aiOn = false
    /// How AI stands, as the digest last said: what is off and why, so what only AI serves can go once it is turned
    /// off.
    private(set) var ai: API.AiStatus?
    /// AI was turned off in Settings: what only AI serves isn't offered, and nothing asks to turn it on.
    var aiTurnedOff: Bool { ai?.isTurnedOff == true }

    // The preferences the list reads (/api/preferences), as the server holds them.
    private(set) var grouped = true
    private(set) var scope = API.SearchScope.all
    private(set) var singleKeys = true

    /// The Inbox's tab. Changing it lists the tab.
    var tab = InboxTab.needs {
        didSet { if tab != oldValue { app?.openList() } }
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
    /// Whether a verb has anything to act on.
    var hasTarget: Bool { !chosenRows.isEmpty || !unheld.isEmpty || app?.selection.copy != nil }
    /// Positions chosen whose rows weren't held when chosen (Select All in a long list): read before a verb acts.
    @ObservationIgnored private(set) var unheld = IndexSet()

    /// What a person was last told, with the Undo it offers.
    private(set) var toast: Toast?
    /// The Move To sheet, while it is open.
    var moving: MoveAsk?
    /// The command palette, while it is open.
    var paletteOpen = false
    /// A search being saved as a view: its query, while the sheet asks its name.
    var savingView: String?
    /// The sender whose card is open.
    var contact: String?
    /// Whether the search field has the focus: `/` and Search (⌘F) put it there.
    var searchFocus = false
    /// A field of the window's is being typed in, where ⌘⌫ deletes text and not mail.
    var typing = false
    /// The rows being dragged from the list, which the rail's folders take (`drop(on:of:)`); none between drags.
    var dragging: [API.FolderRow] = []

    @ObservationIgnored private var gathered: Gathered?
    @ObservationIgnored private var load: MailListModel.Loader?
    @ObservationIgnored private var listNumber = 0
    @ObservationIgnored private var toastNumber = 0
    @ObservationIgnored private var digestSoon: Task<Void, Never>?
    /// What the mailbox last put on Edit ▸ Undo: while it is the next thing undone there, Undo goes through it.
    @ObservationIgnored var offered: String?
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
        more = []
        keys = [:]
        rowMenu = []
        opensBrief = nil
        snoozes = nil
        translates = nil
        keepsLiteral = nil
        translating = nil
        stopsTranslating = nil
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
        readOnly = digest.profile == "read-only"
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
    /// the accounts' names a moment later, and the labels and views after a sync.
    func heard(_ event: CoreEvent) {
        if MailApp.mailChanged.contains(event.name) || event.name == "folder-synced" {
            lane?.refresh()
            Task { await readCounts() }
            digestSoon?.cancel()
            digestSoon = Task {
                try? await Task.sleep(for: .milliseconds(400))
                guard !Task.isCancelled else { return }
                await readDigest()
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
            let gathered = Gathered(read: read, report: report)
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
        let one = rows.count == 1 && unheld.isEmpty ? MailCopy(rows[0]) : nil
        app?.open(one)
        if let one { warmNext(after: one) }
    }

    /// Show today's brief in the pane beside the list: opened on purpose, or arrived at by the arrow keys.
    func openBrief(deliberately: Bool) {
        if let opensBrief { opensBrief(deliberately) } else { app?.place = .desk(.brief) }
    }

    /// Mark no rows but the one open.
    func clearMarks() {
        chose(app?.selection.copy.flatMap(row(of:)).map { [$0] } ?? [])
    }

    /// The rows a verb acts on: those chosen, else the one open. Rows chosen that weren't held are read first.
    func targets() async -> [API.FolderRow] {
        var rows = chosenRows
        if rows.isEmpty, let open = app?.selection.copy, let row = row(of: open) { rows = [row] }
        guard !unheld.isEmpty, let load, let list = app?.list else { return rows }
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

    /// The row the list holds for a copy.
    func row(of copy: MailCopy) -> API.FolderRow? {
        guard let list = app?.list, let position = list.position(of: copy) else { return nil }
        return list.copy(at: position)
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
    func say(_ text: String, detail: String? = nil, undo: String? = nil) {
        toastNumber += 1
        let number = toastNumber
        toast = Toast(text: text, detail: detail, undo: undo)
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

    /// How long words stay, and an Undo offer.
    static let saysMs = 2600
    static let offersMs = 8000
}

/// A move being asked (`MoveAsk`): whose folders it may go to, and the one folder the rows are in, which isn't offered.
struct MoveAsk: Identifiable, Equatable {
    let account: String
    let from: String?
    var id: String { account + "\u{0}" + (from ?? "") }
}

extension MailListModel {
    /// Where a copy is among the rows held.
    func position(of copy: MailCopy) -> Int? {
        var position = 0
        while position < total {
            guard let row = self.copy(at: position) else {
                position = (position / MailListModel.pageSize + 1) * MailListModel.pageSize
                continue
            }
            if MailCopy(row) == copy { return position }
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

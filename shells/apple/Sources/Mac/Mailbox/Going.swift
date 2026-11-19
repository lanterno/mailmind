#if os(macOS)
import AppKit

/// Ways about the mailbox, as the page's app/commands.ts and TopBar.tsx go: to a folder by its role (g then a letter),
/// up and down the list, a search from the search field, and every command there is, which the palette (⌘K) lists.
extension Mailbox {
    /// g then a letter: the folder that plays that role on this account (`ROLE_KEYS`).
    static let roleKeys: [Character: String] = ["i": "inbox", "s": "sent", "a": "archive", "t": "trash", "k": "junk", "d": "drafts"]
    /// g then a letter: a virtual folder (`VIRTUAL_KEYS`).
    static let virtualKeys: [Character: VirtualFolder] = ["z": .snoozed, "w": .waiting, "l": .replyLater]

    /// The account a way to a folder goes to: the place's own, else the first (`account` in useMailbox.ts).
    var goingAccount: String? {
        app?.place?.account ?? accounts.first
    }

    /// Whether the window is over every account, where a virtual folder opens over every account too.
    private var overEveryAccount: Bool {
        switch app?.place {
        case .allInboxes, .documents: true
        case .virtual(_, let account): account == nil
        default: false
        }
    }

    /// Go where g then `key` goes (`goChord`).
    func go(chord key: Character) {
        if let virtual = Self.virtualKeys[key] { return go(virtual) }
        if let role = Self.roleKeys[key] { go(role: role) }
    }

    /// The folder that plays `role` on this account. Over every account, the first account's, as the page's g chords
    /// go (`account ?? accounts[0]`): All accounts brings only the inboxes together, and no route lists another
    /// folder across accounts, so the window says whose folder it went to and why.
    func go(role: String) {
        guard let account = goingAccount else { return }
        guard let found = folder(playing: role, on: account) else {
            say("No \(role) folder on this account", refused: true)
            return
        }
        let fromEveryAccount = overEveryAccount && accounts.count > 1
        let place = Place.folder(account: account, folder: found.name)
        app?.place = place
        if fromEveryAccount {
            let title = place.title(folders: folders(of: account))
            say(String(localized: "\(name(of: account))’s \(title): All accounts brings only the inboxes together"))
        }
    }

    /// A virtual folder: over every account from All Inboxes, else the account's own (`goToFolder`).
    func go(_ virtual: VirtualFolder) {
        app?.place = .virtual(virtual, account: overEveryAccount ? nil : goingAccount)
    }

    /// The folder that plays `role` on `account`. Gmail archives into All Mail.
    private func folder(playing role: String, on account: String) -> API.RailFolder? {
        let held = folders(of: account)
        return held.first { $0.role == role } ?? (role == "archive" ? held.first { $0.role == "all" } : nil)
    }

    /// Where g then each letter goes from here, for the palette to show beside the place ("g i"): only what the keys
    /// reach from where the window is, and nothing while single keys are off.
    func chordsHere() -> [Place: String] {
        guard singleKeys else { return [:] }
        var chords: [Place: String] = [:]
        for key in "isatkdzwl" {
            let place: Place? = if let virtual = Self.virtualKeys[key] {
                .virtual(virtual, account: overEveryAccount ? nil : goingAccount)
            } else if let role = Self.roleKeys[key], let account = goingAccount,
                      let found = folder(playing: role, on: account) {
                .folder(account: account, folder: found.name)
            } else {
                nil
            }
            if let place, chords[place] == nil { chords[place] = "g \(key)" }
        }
        return chords
    }

    // MARK: Up and down the list

    /// The row `by` rows from the open one, or the first. Up from the Inbox's first row is today's brief, over it, as
    /// the up arrow goes there; at either end of the list, the list says so, as n and p do. While rows are marked
    /// the marks stay, and the row moved to opens beside them.
    func move(by: Int) {
        guard let list = app?.list, list.total > 0 else { return }
        let at = app?.selection.copy.flatMap { list.position(of: $0) } ?? markedEnd(by, in: list)
        if by < 0, at == 0, showsBrief, !marking { return openBrief(deliberately: false) }
        // On the brief already, there is nothing above it.
        if by < 0, at == nil, app?.place == .desk(.brief) { return }
        if let at, at + by < 0 || at + by >= list.total {
            return say(by > 0 ? String(localized: "No more messages below") : String(localized: "No more messages above"),
                       refused: true)
        }
        let target = at.map { $0 + by } ?? 0
        if let row = list.copy(at: target) { go(to: row) }
    }

    /// Open `row` as j, k, n and p go to it: chosen alone, or beside the rows marked, which stay marked.
    private func go(to row: API.FolderRow) {
        if marking { openBeside(row) } else { chose([row]) }
    }

    /// Where j and k start while rows are marked and none is open: from the last mark going down, the first going up.
    private func markedEnd(_ by: Int, in list: MailListModel) -> Int? {
        guard marking else { return nil }
        let positions = chosenRows.compactMap { list.position(of: MailCopy($0)) }
        return by > 0 ? positions.max() : positions.min()
    }

    /// Whether today's brief stands over the list, as its first row: in an Inbox, where the desk has joined.
    private var showsBrief: Bool {
        opensBrief != nil && app?.listed?.isInbox == true
    }

    /// The next unread row above or below the open one, among the rows held. False when there is none.
    @discardableResult
    func moveUnread(by: Int) -> Bool {
        guard let list = app?.list else { return false }
        let at = app?.selection.copy.flatMap { list.position(of: $0) } ?? markedEnd(by, in: list)
            ?? (by > 0 ? -1 : list.total)
        var position = at + by
        while position >= 0, position < list.total, let row = list.copy(at: position) {
            if !row.seen {
                go(to: row)
                return true
            }
            position += by
        }
        say(by > 0 ? String(localized: "No more unread below") : String(localized: "No more unread above"), refused: true)
        return false
    }

    // MARK: Searching

    /// What the search field's Return does (TopBar.tsx): `?words` asks your inbox, `!words` plans a bulk action, and
    /// anything else searches, from the account the window is on. With AI turned off, everything searches.
    enum Submitted: Equatable {
        case search(Search)
        case question(String)
        case command(String)
        /// Nothing to search: back to where the search came from.
        case leave
    }

    /// What `text` asks for, as the page reads the box.
    func submitted(_ text: String) -> Submitted {
        let q = text.trimmingCharacters(in: .whitespacesAndNewlines)
        let words = String(q.dropFirst()).trimmingCharacters(in: .whitespacesAndNewlines)
        if !aiTurnedOff, q.hasPrefix("?") { return words.isEmpty ? .leave : .question(words) }
        if !aiTurnedOff, q.hasPrefix("!") { return .command(words) }
        if q.isEmpty { return .leave }
        if let held = app?.place?.search, let thread = held.thread {
            return .search(Search(q: q, account: held.account, thread: thread))
        }
        let account: String = switch app?.place {
        case .folder(let account, _): account
        case .search(let search): search.account
        default: ""
        }
        return .search(Search(q: q, account: account))
    }

    /// Search for `text`, or hand a question or a plan to the assistant.
    func submit(_ text: String) {
        switch submitted(text) {
        case .search(let search):
            app?.place = .search(search)
            translate(search)
        case .question(let words): asks?(words)
        case .command(let words): plans?(words)
        case .leave: leaveSearch()
        }
    }

    /// A sentence searched for as typed stands until the assistant says what search it means, which then takes its
    /// place with the sentence on its chip (`ProseSearch`); a search held to a conversation stays as typed.
    private func translate(_ search: Search) {
        guard search.thread == nil, let translates else { return }
        Task { [weak self] in
            guard let filter = await translates(search.q), self?.app?.place == .search(search) else { return }
            var meant = search
            meant.q = filter
            meant.asked = search.q
            self?.app?.place = .search(meant)
        }
    }

    /// ↓ from the search field, with no suggestion under it: the keyboard goes to the list in `window`, and the list's
    /// first row opens unless a message is open, as the empty pane says.
    func enterList(in window: NSWindow?) {
        if let window, let table = ListTable.find(in: window.contentView) { window.makeFirstResponder(table) }
        if app?.selection.copy == nil, !marking { move(by: 1) }
    }

    /// Esc on an answer: back to where the question was asked from, as the page's Esc stops asking, and the list there
    /// takes the keyboard as it shows. Whether the window was on the answer.
    @discardableResult
    func leaveAsk() -> Bool {
        guard let app, app.place == .desk(.ask) else { return false }
        app.place = askedFrom ?? .allInboxes
        askedFrom = nil
        return true
    }

    /// Out of a search, back to the Inbox it was asked from (`leave`); out of an answer, back to where it was asked.
    func leaveSearch() {
        stopsTranslating?()
        if leaveAsk() { return }
        guard case .search(let search) = app?.place else { return }
        app?.place = search.account.isEmpty ? .allInboxes : .folder(account: search.account, folder: "INBOX")
    }

    /// What the server finishes the word being typed with (`/api/suggest`), from the grammar and who has written.
    func suggestions(for text: String) async -> [API.SearchSuggestion] {
        guard let calls, !text.trimmingCharacters(in: .whitespaces).isEmpty,
              let read = try? await calls.get(API.Get.Suggest.self, ["q": text]) else { return [] }
        return read.items
    }

    /// Save the search as a view of the rail's (`saveSearch`).
    func saveView(named name: String, query q: String) {
        let name = name.trimmingCharacters(in: .whitespaces), q = q.trimmingCharacters(in: .whitespaces)
        savingView = nil
        guard let calls, !name.isEmpty, !q.isEmpty else { return }
        Task {
            do {
                views = try await calls.post(API.Post.Views.self, API.ViewChange(name: name, q: q, verb: .create)).views
                say("Saved view · \(name)")
            } catch let problem as Problem where problem.isRefusal {
                say(problem.detail ?? String(localized: "Couldn’t save"), refused: true)
            } catch {
                say(String(localized: "Couldn’t save view"))
            }
        }
    }

    /// Take a view a person saved off the rail.
    func removeView(_ view: API.ListedView) {
        guard let calls else { return }
        Task {
            do {
                views = try await calls.post(API.Post.Views.self, API.ViewChange(id: view.id, verb: .delete)).views
                say(String(localized: "View removed"))
            } catch {
                say(String(localized: "Couldn’t remove view"))
            }
        }
    }

    /// Whether the search on screen is a view already, so nothing offers to save it again (`onSavedView`).
    var onSavedView: Bool {
        guard let search = app?.place?.search else { return false }
        return views.contains { viewQuery($0) == search.q }
    }

    // MARK: Every command

    /// Every command there is from here, for the palette: going places, the views, and the verbs on what is chosen or
    /// open (`buildCommands`). Each runs as its menu item or key does.
    func commands() -> [MailCommand] {
        var out: [MailCommand] = []
        let some = app?.selection.copy != nil || !marked.isEmpty
        let chords = chordsHere()
        // A way to a place, with the keys that go there too.
        func going(_ id: String, _ label: String, to place: Place) -> MailCommand {
            MailCommand(id: id, group: .navigation, label: label, hint: chords[place]) { [weak self] in
                self?.app?.place = place
            }
        }
        out.append(going("all-inboxes", "All inboxes", to: .allInboxes))
        for virtual in VirtualFolder.allCases {
            out.append(going("across:\(virtual.rawValue)", "All inboxes / \(virtual.title)",
                             to: .virtual(virtual, account: nil)))
        }
        for account in accounts {
            let name = name(of: account)
            out.append(going("acct:\(account)", "Account · \(name)", to: .folder(account: account, folder: "INBOX")))
            for folder in folders(of: account) {
                out.append(going("fold:\(account):\(folder.name)", "\(name) / \(Words.role(folder))",
                                 to: .folder(account: account, folder: folder.name)))
            }
            for virtual in [VirtualFolder.snoozed, .waiting] {
                out.append(going("fold:\(account):\(virtual.rawValue)", "\(name) / \(virtual.title)",
                                 to: .virtual(virtual, account: account)))
            }
        }
        out.append(MailCommand(id: "docs", group: .navigation, label: "Documents — find any attachment") { [weak self] in
            self?.app?.place = .documents
        })

        out.append(MailCommand(id: "move-down", group: .actions, label: "Next message", hint: "j") { [weak self] in self?.move(by: 1) })
        out.append(MailCommand(id: "move-up", group: .actions, label: "Previous message", hint: "k") { [weak self] in self?.move(by: -1) })
        out.append(MailCommand(id: "next-unread", group: .actions, label: "Next unread", hint: "n") { [weak self] in self?.moveUnread(by: 1) })
        out.append(MailCommand(id: "prev-unread", group: .actions, label: "Previous unread", hint: "p") { [weak self] in self?.moveUnread(by: -1) })
        if some {
            out.append(MailCommand(id: "archive", group: .actions, label: "Archive", hint: "⌃⌘A") { [weak self] in self?.file(.archive) })
            out.append(MailCommand(id: "trash", group: .actions, label: "Delete", hint: "⌘⌫") { [weak self] in self?.file(.trash) })
            out.append(MailCommand(id: "junk", group: .actions, label: "Mark junk", hint: "⇧⌘J") { [weak self] in self?.file(.junk) })
            out.append(MailCommand(id: "move-to", group: .actions, label: "Move to folder…", hint: "⇧⌘M") { [weak self] in self?.askMove() })
            let unstars = Marks(shownRows).unstars
            out.append(MailCommand(id: "star", group: .actions, label: unstars ? "Unstar" : "Star", hint: "⇧⌘L") {
                [weak self] in self?.star(on: !unstars)
            })
            out.append(MailCommand(id: "unread", group: .actions, label: "Mark unread", hint: "⇧⌘U") { [weak self] in self?.setSeen(false) })
        }
        if let open = app?.selection.copy, row(of: open) != nil {
            out.append(MailCommand(id: "mark", group: .actions, label: "Select", hint: "x") { [weak self] in self?.markOpen() })
        }
        out.append(MailCommand(id: "undo", group: .commands, label: "Undo last action", hint: "⌘Z") { [weak self] in self?.undo() })
        for view in views {
            let lane = Lane(view: view.id)
            out.append(MailCommand(id: "view:\(view.id)", group: .views, label: view.name + (lane == nil ? "" : " (lane)")) { [weak self] in
                self?.app?.place = .search(Search(q: viewQuery(view), account: self?.searchAccount ?? "", lane: lane))
            })
        }
        out.append(MailCommand(id: "grouping", group: .commands, label: "Toggle conversations / messages") { [weak self] in
            guard let self else { return }
            setGrouped(!grouped)
        })
        if let search = app?.place?.search, !onSavedView {
            out.append(MailCommand(id: "save-view", group: .commands, label: "Save this search as a view", hint: "v") { [weak self] in
                self?.savingView = search.q
            })
        }
        // The other areas' commands join here, each as its menu item runs it.
        out += more.flatMap { $0(self) }
        return out
    }

    /// The account a view searches from: the one the window is on, as the rail's views do.
    var searchAccount: String {
        app?.place?.account ?? ""
    }
}

/// One thing the palette can do (`Command`): what it says, its group, the key that does it too, and what it runs.
struct MailCommand: Identifiable {
    enum Group: String, CaseIterable {
        case navigation = "Navigation"
        case views = "Views"
        case actions = "Actions"
        case commands = "Commands"
    }

    let id: String
    let group: Group
    let label: String
    var hint: String?
    let run: @MainActor () -> Void
}
#endif

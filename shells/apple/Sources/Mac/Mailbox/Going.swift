#if os(macOS)
import Foundation

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

    /// The folder that plays `role` on this account. Gmail archives into All Mail.
    func go(role: String) {
        guard let account = goingAccount else { return }
        let held = folders(of: account)
        guard let found = held.first(where: { $0.role == role }) ?? (role == "archive" ? held.first { $0.role == "all" } : nil) else {
            say("No \(role) folder on this account")
            return
        }
        app?.place = .folder(account: account, folder: found.name)
    }

    /// A virtual folder: over every account from All Inboxes, else the account's own (`goToFolder`).
    func go(_ virtual: VirtualFolder) {
        app?.place = .virtual(virtual, account: overEveryAccount ? nil : goingAccount)
    }

    // MARK: Up and down the list

    /// The row `by` rows from the open one, or the first.
    func move(by: Int) {
        guard let list = app?.list, list.total > 0 else { return }
        let at = app?.selection.copy.flatMap { list.position(of: $0) }
        let target = at.map { max(0, min(list.total - 1, $0 + by)) } ?? 0
        if let row = list.copy(at: target) { chose([row]) }
    }

    /// The next unread row above or below the open one, among the rows held. False when there is none.
    @discardableResult
    func moveUnread(by: Int) -> Bool {
        guard let list = app?.list else { return false }
        let at = app?.selection.copy.flatMap { list.position(of: $0) } ?? (by > 0 ? -1 : list.total)
        var position = at + by
        while position >= 0, position < list.total, let row = list.copy(at: position) {
            if !row.seen {
                chose([row])
                return true
            }
            position += by
        }
        say(by > 0 ? String(localized: "No more unread below") : String(localized: "No more unread above"))
        return false
    }

    // MARK: Searching

    /// What the search field's Return does (TopBar.tsx): `?words` asks your inbox, `!words` plans a bulk action, and
    /// anything else searches, from the account the window is on.
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
        if q.hasPrefix("?") { return words.isEmpty ? .leave : .question(words) }
        if q.hasPrefix("!") { return .command(words) }
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

    /// Out of a search, back to the Inbox it was asked from (`leave`).
    func leaveSearch() {
        stopsTranslating?()
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
                say(problem.detail ?? String(localized: "Couldn’t save"))
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
        out.append(MailCommand(id: "all-inboxes", group: .navigation, label: "All inboxes") { [weak self] in
            self?.app?.place = .allInboxes
        })
        for virtual in VirtualFolder.allCases {
            out.append(MailCommand(id: "across:\(virtual.rawValue)", group: .navigation, label: "All inboxes / \(virtual.title)") { [weak self] in
                self?.app?.place = .virtual(virtual, account: nil)
            })
        }
        for account in accounts {
            let name = name(of: account)
            out.append(MailCommand(id: "acct:\(account)", group: .navigation, label: "Account · \(name)") { [weak self] in
                self?.app?.place = .folder(account: account, folder: "INBOX")
            })
            for folder in folders(of: account) {
                out.append(MailCommand(id: "fold:\(account):\(folder.name)", group: .navigation, label: "\(name) / \(Words.role(folder))") { [weak self] in
                    self?.app?.place = .folder(account: account, folder: folder.name)
                })
            }
            for virtual in [VirtualFolder.snoozed, .waiting] {
                out.append(MailCommand(id: "fold:\(account):\(virtual.rawValue)", group: .navigation, label: "\(name) / \(virtual.title)") { [weak self] in
                    self?.app?.place = .virtual(virtual, account: account)
                })
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
            out.append(MailCommand(id: "star", group: .actions, label: "Star", hint: "⇧⌘L") { [weak self] in self?.star() })
            out.append(MailCommand(id: "unread", group: .actions, label: "Mark unread", hint: "⇧⌘U") { [weak self] in self?.setSeen(false) })
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

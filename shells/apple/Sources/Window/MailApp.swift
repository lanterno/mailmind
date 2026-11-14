import Foundation
import Observation

/// The window's state: the core opened over this process's home, the accounts and their folders the sidebar shows,
/// where the window is (`place`), the list there and the message chosen in it (`selection`), and the reader. What
/// changed is heard on the event stream, and what it touches is read again, as the page's realtime.ts does.
@MainActor
@Observable
final class MailApp {
    enum Stage: Equatable {
        case opening
        case open
        case failed(String)
    }

    /// The topics after which the lists and the folders' counts are read again (`MAIL_CHANGED` in app/realtime.ts),
    /// with a folder's first scan that read it.
    static let mailChanged: Set<String> = [
        "new-mail", "snooze-wake", "mute", "reconcile", "flags-changed", "followup", "undo-done", "commands-changed",
        "resync", "sync-done",
    ]

    private(set) var stage = Stage.opening
    private(set) var accounts: [API.AccountRow] = []
    private(set) var folders: [String: [API.RailFolder]] = [:]
    /// Where the window is. Choosing a place opens its list.
    var place: Place? = .allInboxes {
        didSet { if place != oldValue { openList() } }
    }
    let selection = Selection()
    private(set) var list: MailListModel?
    let reader = ReaderModel()
    @ObservationIgnored private(set) var core: Core?
    @ObservationIgnored private var listening: Task<Void, Never>?

    /// Open the core over this process's home, then the sidebar and the first list, then listen.
    func start() async {
        guard stage == .opening, core == nil else { return }
        do {
            let core = try await Core.open(try CoreHome.ofThisProcess())
            self.core = core
            reader.core = core
            stage = .open
            openList()
            await readSidebar()
            listen(to: core)
        } catch {
            stage = .failed((error as? Problem)?.description ?? String(describing: error))
        }
    }

    /// The accounts, then each one's folders.
    func readSidebar() async {
        guard let core else { return }
        do {
            accounts = try await core.get(API.Get.Accounts.self).accounts
        } catch {
            return
        }
        for account in accounts {
            guard let email = account.email else { continue }
            if let read = try? await core.get(API.Get.Folders.self, ["account": email]) {
                folders[email] = read.folders
            }
        }
    }

    /// Choose the message at `position` in the list, or none.
    func choose(_ position: Int?) {
        let row = position.flatMap { list?.copy(at: $0) }
        let copy = row.map(MailCopy.init)
        guard copy != selection.copy else { return }
        selection.copy = copy
        reader.open(copy)
    }

    private func openList() {
        guard let core, let place else {
            list = nil
            return
        }
        let load: MailListModel.Loader
        switch place {
        case .allInboxes:
            load = { offset, limit in
                let read = try await core.get(API.Get.Inboxes.self, ["offset": String(offset), "limit": String(limit)])
                return .init(total: read.total, rows: read.items)
            }
        case .folder(let account, let folder):
            load = { offset, limit in
                let read = try await core.get(API.Get.Messages.self, [
                    "account": account, "folder": folder, "offset": String(offset), "limit": String(limit),
                ])
                switch read {
                case .folderListing(let listing): return .init(total: listing.total, rows: listing.items)
                default: return .init(total: 0, rows: [])
                }
            }
        }
        let list = MailListModel(selection: selection, load: load)
        self.list = list
        list.start()
    }

    /// A folder's first scan that read it: one that failed leaves the list as it is, or reading it again would scan
    /// the folder again for as long as its server says no.
    private static func readAFolder(_ event: CoreEvent) -> Bool {
        event.name == "folder-synced" && event.decoded([String: JSONValue].self)?["ok"] == .bool(true)
    }

    private func listen(to core: Core) {
        listening = Task { [weak self] in
            guard let events = try? await core.events() else { return }
            for await event in events {
                guard let self else { return }
                if Self.mailChanged.contains(event.name) || Self.readAFolder(event) {
                    self.list?.refresh()
                    await self.readSidebar()
                }
            }
        }
    }
}

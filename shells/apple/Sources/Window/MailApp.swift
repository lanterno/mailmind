import Foundation
import Observation

/// The window's state: the core opened over this process's home, where the window is (`place`), the list there and
/// the message chosen in it (`selection`), and the reader. What changed is heard on the event stream, and what it
/// touches is read again, as the page's realtime.ts does; on the Mac the mailbox reads the sidebar's accounts,
/// folders and counts.
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

    /// The one this process runs. Every window the mail scene opens shows it, so the window closed and opened again
    /// from the Dock finds the core already open: one core over the data folder, never a second with its own sync.
    static let shared = MailApp()

    /// Opens a home's core: the host's, or a test's in its place.
    typealias Opener = @Sendable (CoreHome) async throws -> Core

    private(set) var stage = Stage.opening
    /// Where the window is. Choosing a place opens its list; the brief, in the pane beside the Inbox, keeps the
    /// Inbox's list and closes the message open, as the pane is the brief's.
    var place: Place? = .allInboxes {
        didSet {
            guard place != oldValue else { return }
            let was = listed
            listed = place.map { Self.listed($0, beside: was) }
            if listed != was { openList() }
            if let place, place != listed { closeMessage() }
        }
    }
    /// The place whose list the window shows: the place's own (`Place.listed`), but beside the brief the Inbox the
    /// person was in, one account's or every account's, so opening the brief from its row keeps the list as it was.
    private(set) var listed: Place? = .allInboxes
    let selection = Selection()
    private(set) var list: MailListModel?
    let reader = ReaderModel()
    @ObservationIgnored private(set) var core: Core?
    @ObservationIgnored private var listening: Task<Void, Never>?
    @ObservationIgnored private let home: () throws -> CoreHome
    @ObservationIgnored private let opener: Opener
    /// Set while the core is being opened, so a second window's start leaves it to the first rather than opening
    /// another.
    @ObservationIgnored private var opening = false
    #if os(macOS)
    /// The sidebar's places and counts, every place's list, the rows chosen and the verbs on them (Mac/Mailbox).
    @ObservationIgnored private(set) lazy var mailbox = Mailbox(app: self)
    #endif

    /// `home` picks the data folder and `open` opens a core over it: this process's and the host's, unless a test
    /// gives its own.
    init(home: @escaping () throws -> CoreHome = { try CoreHome.ofThisProcess() },
         open: @escaping Opener = { try await Core.open($0) })
    {
        self.home = home
        opener = open
    }

    /// Open the core over this process's home, then the first list and the sidebar, then listen. Once: a start while
    /// one is opening, or after one opened, does nothing.
    func start() async {
        guard stage == .opening, core == nil, !opening else { return }
        opening = true
        defer { opening = false }
        do {
            let core = try await opener(try home())
            self.core = core
            reader.core = core
            stage = .open
            openList()
            #if os(macOS)
            await mailbox.refresh()
            #endif
            listen(to: core)
        } catch {
            stage = .failed((error as? Problem)?.description ?? String(describing: error))
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

    /// No message open, and on the Mac no rows chosen in the list.
    private func closeMessage() {
        #if os(macOS)
        mailbox.chose([])
        #else
        selection.copy = nil
        reader.open(nil)
        #endif
    }

    /// The list a place shows, when the list before it was `was`: an Inbox stays beside the brief; any other list
    /// gives way to every account's Inbox.
    static func listed(_ place: Place, beside was: Place?) -> Place {
        guard place.listed != place else { return place }
        if let was, was.isInbox { return was }
        return place.listed
    }

    /// Open the list the place shows, read as the mailbox reads each of its places (Mac/Mailbox/Mailbox.swift), or none
    /// for a place that isn't a list of mail. The mailbox opens it again when its tab or grouping changes.
    func openList() {
        #if os(macOS)
        let load = listed.flatMap { mailbox.loader(for: $0) }
        #else
        let load: MailListModel.Loader? = nil
        #endif
        guard let load else {
            list = nil
            return
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
                #if os(macOS)
                self.mailbox.heard(event)
                #endif
                if Self.mailChanged.contains(event.name) || Self.readAFolder(event) {
                    self.list?.refresh()
                }
            }
        }
    }
}

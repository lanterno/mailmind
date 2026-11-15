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

    private(set) var stage = Stage.opening
    /// Where the window is. Choosing a place opens its list.
    var place: Place? = .allInboxes {
        didSet { if place != oldValue { openList() } }
    }
    let selection = Selection()
    private(set) var list: MailListModel?
    let reader = ReaderModel()
    @ObservationIgnored private(set) var core: Core?
    @ObservationIgnored private var listening: Task<Void, Never>?
    #if os(macOS)
    /// The sidebar's places and counts, every place's list, the rows chosen and the verbs on them (Mac/Mailbox).
    @ObservationIgnored private(set) lazy var mailbox = Mailbox(app: self)
    #endif

    /// Open the core over this process's home, then the first list and the sidebar, then listen.
    func start() async {
        guard stage == .opening, core == nil else { return }
        do {
            let core = try await Core.open(try CoreHome.ofThisProcess())
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

    /// Open the place's list, read as the mailbox reads each of its places (Mac/Mailbox/Mailbox.swift), or none for a
    /// place that isn't a list of mail. The mailbox opens it again when its tab or grouping changes.
    func openList() {
        #if os(macOS)
        let load = place.flatMap { mailbox.loader(for: $0) }
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

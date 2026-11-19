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
    /// A round of the AI's summaries landed (`summarized`), with no mail changing: what needs you and what can wait
    /// changed, which the list, the Inbox's tabs, the rail and the brief count. Those are read again, as the page's
    /// realtime.ts reads them; the folders' counts are of unread mail, and stay.
    static let summarized = "summarized"

    /// The one this process runs. Every window the mail scene opens shows it, so the window closed and opened again
    /// from the Dock finds the core already open: one core over the data folder, never a second with its own sync.
    static let shared = MailApp()

    /// Opens a home's core: the host's, or a test's in its place.
    typealias Opener = @Sendable (CoreHome) async throws -> Core

    private(set) var stage = Stage.opening
    /// The data folder the core opens, once chosen: where its log is, and what Start Fresh moves the database out of.
    private(set) var folder: URL?
    /// Where the window is. Choosing a place opens its list; the brief, in the pane beside the Inbox, keeps the
    /// Inbox's list and closes the message open, as the pane is the brief's. A page across the window (the gate, the
    /// paper trail) closes it too: it spans the window only while no message is open, and one left open from the
    /// place before would keep it in the list's room, beside a message that isn't its own. Any other list keeps the
    /// message left open only if it holds it (`keepIfListed`).
    var place: Place? = .allInboxes {
        didSet {
            guard place != oldValue else { return }
            let was = listed
            listed = place.map { Self.listed($0, beside: was) }
            if listed != was { openList() }
            if let place, place != listed || place.spansTheWindow {
                closeMessage()
            } else if listed != was, let open = selection.copy {
                keepIfListed(open)
            }
        }
    }
    /// The place whose list the window shows: the place's own (`Place.listed`), but beside the brief the Inbox the
    /// person was in, one account's or every account's, so opening the brief from its row keeps the list as it was.
    private(set) var listed: Place? = .allInboxes
    let selection = Selection()
    private(set) var list: MailListModel?
    let reader = ReaderModel()
    /// Counted up each time a message is opened on purpose, so the message left open by a change of place is closed
    /// only if nothing was opened since (`keepIfListed`): a notification opens its message once at its folder.
    @ObservationIgnored var openings = 0
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
    /// Tells the core of a wake from sleep and of a network that changed.
    @ObservationIgnored private var waking: Waking?
    #endif

    /// `home` picks the data folder and `open` opens a core over it: this process's and the host's, unless a test
    /// gives its own.
    init(home: @escaping () throws -> CoreHome = { try CoreHome.ofThisProcess() },
         open: @escaping Opener = { try await Core.open($0) })
    {
        self.home = home
        opener = open
    }

    /// Open the core over this process's home, listen, then read the first list and the sidebar. Once: a start while
    /// one is opening, or after one opened, does nothing.
    func start() async {
        guard stage == .opening, core == nil, !opening else { return }
        opening = true
        defer { opening = false }
        do {
            let home = try home()
            folder = URL(filePath: home.folder, directoryHint: .isDirectory)
            let core = try await opener(home)
            self.core = core
            reader.core = core
            // Heard from before the first reads: the core syncs as it opens and keeps no event for a later listener,
            // so a first sync that ended while the rail was read would leave the rail and list as they were.
            let events = try? await core.events()
            stage = .open
            openList()
            #if os(macOS)
            // A wake or a network that changed: IDLE's connections may be dead without a word, so the core opens them
            // afresh and syncs now.
            let waking = Waking { [weak core] in core?.woke() }
            waking.start()
            self.waking = waking
            await mailbox.refresh()
            #endif
            if let events { listen(to: events) }
        } catch {
            stage = .failed(Self.words(error))
        }
    }

    /// Open again after a failure, as Start Fresh does once the database has moved aside.
    func startAgain() async {
        guard case .failed = stage else { return }
        stage = .opening
        await start()
    }

    /// What a person is told of a core that didn't open: the core's own words, never a Swift type's name. Any other
    /// error says only that something unexpected happened, never its own text: a panic in the core crosses into Swift
    /// carrying its message (UniFFI's `rustPanic`), which can quote mail, and the window and Copy Details show this.
    static func words(_ error: any Error) -> String {
        switch error {
        case HostProblem.NotOpened(let why): why
        case let problem as Problem: problem.description
        case let named as NoHomeNamed: named.description
        default: unexpected
        }
    }

    /// What a failure the core didn't word says.
    static let unexpected = String(localized: """
        MailMind ran into something it didn’t expect as it opened. Open MailMind again; if this keeps happening, copy \
        the details and send them to whoever gave you MailMind.
        """)

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

    /// The message `open`, left open as the window went to a place with a list of its own, stays only where that list
    /// holds it, as the page's reader closes on going elsewhere: once the list's first rows come, or a lane's hits, it
    /// closes unless they hold it, so Sent never shows the Inbox's message nor an empty Snoozed a message it hasn't.
    /// A message opened since (a notification's, in the folder it opened) is left alone. An answer stands in the list's
    /// place and leaves the pane reading; the Document Hub holds no mail of its own.
    private func keepIfListed(_ open: MailCopy) {
        #if os(macOS)
        let turn = openings
        let stays: @MainActor (Bool) -> Void = { [weak self] holds in
            guard let self, self.selection.copy == open, self.openings == turn, !holds else { return }
            self.closeMessage()
        }
        switch listed {
        case .desk?:
            return
        case .documents?:
            closeMessage()
        default:
            if let list {
                list.onceRead = { [weak self, weak list] in
                    guard let list, self?.list === list else { return }
                    stays(list.position(of: open) != nil)
                }
            } else if let lane = mailbox.lane {
                lane.onceLoaded = { [weak self, weak lane] in
                    guard let lane, self?.mailbox.lane === lane else { return }
                    stays(lane.hits.contains { MailCopy(API.FolderRow($0)) == open })
                }
            } else {
                closeMessage()
            }
        }
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

    /// An account was added or removed (Settings, or the first run): the rail and the list read again now, rather than
    /// at the end of the new account's first sync. It starts the reads and returns at once, so the sheet that saved
    /// the account isn't held on them: a new account's folders are read live from its server, which can take a while,
    /// or up to a minute when it doesn't answer. Who the accounts are and the list are read first, so the rail shows
    /// the account and its Inbox says it is being read for the first time; its folders, counts, labels and views
    /// follow. A place of an account that is gone, or its Inbox listed beside the brief, gives way to every account's
    /// Inbox. The task is a test's to wait on.
    @discardableResult
    func accountsChanged() -> Task<Void, Never> {
        Task {
            #if os(macOS)
            await mailbox.readDigest()
            let was = listed
            // The list beside the brief is an account's Inbox while the place names none, and a Screener names its
            // account apart from `Place.account`: any of them gone, the window goes to every account's Inbox rather
            // than keep showing an account that isn't there.
            var named = [place?.account, listed?.account].compactMap { $0 }
            if case .desk(.screener(let account))? = place { named.append(account) }
            if named.contains(where: { !mailbox.accounts.contains($0) }) { place = .allInboxes }
            if listed == was { list?.refresh() }   // else the place's change opened a list of its own
            await mailbox.readCounts()
            await mailbox.readLabels()
            await mailbox.readViews()
            #else
            list?.refresh()
            #endif
        }
    }

    /// A folder's first scan that read it: one that failed leaves the list as it is, or reading it again would scan
    /// the folder again for as long as its server says no.
    private static func readAFolder(_ event: CoreEvent) -> Bool {
        event.name == "folder-synced" && event.decoded([String: JSONValue].self)?["ok"] == .bool(true)
    }

    /// Whether the list on screen is read again after `event`. An Inbox's first scan says no new-mail, since none of
    /// what it stores is new, so the list doesn't wait for one: the end of the sync that stored its newest mail
    /// (`sync-done`) reads it again, and so does each step of its older mail as it is stored (`folder-synced`). Summaries
    /// landing change which rows need you and what each says, though no mail changed.
    static func refreshesTheList(_ event: CoreEvent) -> Bool {
        mailChanged.contains(event.name) || event.name == summarized || readAFolder(event)
    }

    /// What an event touches, read again: the mailbox's rail and lists, and the list on screen. The stream's listener
    /// calls it; so does a test, with events of its own.
    func heard(_ event: CoreEvent) {
        #if os(macOS)
        mailbox.heard(event)
        #endif
        if Self.refreshesTheList(event) {
            list?.refresh()
        }
    }

    private func listen(to events: AsyncStream<CoreEvent>) {
        listening = Task { [weak self] in
            for await event in events {
                guard let self else { return }
                self.heard(event)
            }
        }
    }
}

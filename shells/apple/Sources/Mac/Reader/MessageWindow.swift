#if os(macOS)
import AppKit
import SwiftUI

/// A message in a window of its own, as a double-click on a row opens one: the reader's document for that copy, on
/// the pane's paper under a bar for the traffic lights, in the light the person chose. It speaks the core the mail
/// window opened, the process's one (`MailApp.shared`), and never opens one of its own: a window that opens before
/// that core is open, or once it didn't open, closes. The Message menu and its keys act on its message alone
/// (`MessageWindowVerbs`), and once the message has left, the window closes.
struct MessageWindow: View {
    static let scene = "message"
    /// The bar the traffic lights stand on.
    static let barHeight: CGFloat = 40
    let copy: MailCopy?
    /// The process's one app, whose core the window speaks.
    var app = MailApp.shared
    @State private var reader = ReaderModel()
    @State private var find = ReaderFind()
    @State private var verbs: MessageWindowVerbs?
    @Environment(\.dismiss) private var dismiss

    /// The core a message's window speaks: `app`'s once it is open, else none.
    static func core(of app: MailApp) -> Core? {
        app.stage == .open ? app.core : nil
    }

    var body: some View {
        if let copy, let core = Self.core(of: app) {
            MessageWindowContent(core: core, copy: copy, reader: reader, find: find, verbs: verbs)
                .task(id: copy) {
                    reader.core = core
                    reader.open(copy)
                    let made = MessageWindowVerbs(copy: copy, mailbox: app.mailbox, reader: reader, routes: core)
                    verbs = made
                    await made.reread()
                }
                // Filed, deleted, junked, moved or snoozed, the message has left: its window goes with it, and the
                // mail window says so, with Undo.
                .onChange(of: verbs?.left == true) { _, left in if left { dismiss() } }
        } else {
            Color.clear
                .frame(width: 1, height: 1)
                .onAppear { dismiss() }
        }
    }
}

extension FocusedValues {
    /// The message window in front, whose message the Message menu's verbs act on in place of the mail window's.
    @Entry var messageWindow: MessageWindowVerbs?
}

/// The verbs on a message in a window of its own, as the Message menu and its keys reach them there: the mailbox's
/// (`fileNow`, `moveNow`, `setSeenNow`, `starNow`) given this message as their rows, and attention's on it alone, so
/// the mail window's open or chosen message is never read or acted on. What they say, and the Undo they offer, the
/// mail window's toast shows. The message stands for itself, as the window shows it, never a whole conversation.
@MainActor
@Observable
final class MessageWindowVerbs {
    let copy: MailCopy
    @ObservationIgnored let mailbox: Mailbox
    @ObservationIgnored let reader: ReaderModel
    /// Snooze, mute and Reply Later on this message, over the same core, saying what they did where the mailbox
    /// says it.
    let attention: AttentionModel
    /// How it stands, as the server last said: what the menus' Star and Mark as Read say they will do.
    private(set) var standing: API.FolderRow?
    /// Move To's folders, while the window asks where.
    var moving: MoveAsk?
    /// Text is being typed in the window (the reply box, Find's field, a dialog's), where ⌘⌫ deletes text and not
    /// the message: its window's `TypingWatch` says.
    var typing = false
    /// The message has left (filed, deleted, junked, moved, snoozed): the window closes.
    private(set) var left = false

    init(copy: MailCopy, mailbox: Mailbox, reader: ReaderModel, routes: (any RouteCaller)?) {
        self.copy = copy
        self.mailbox = mailbox
        self.reader = reader
        let notices = DeskNotices()
        attention = AttentionModel(link: CoreLink(routes), notices: notices)
        notices.relay = { [weak mailbox] text, undoable in
            mailbox?.say(text, undo: undoable ? String(localized: "Undo") : nil)
        }
        attention.changed = { [weak mailbox] in mailbox?.readAgain() }
        // Snoozed, it leaves: the mail window moves on if it had it open, and this window closes.
        attention.leaving = { [weak self, weak mailbox] copies in
            let there = mailbox?.leaving(copies)
            return {
                there?()
                self?.left = true
            }
        }
    }

    /// Whether a verb acts now: not once the message has left, nor while a dialog asks something of the window.
    var acting: Bool { !left && moving == nil && attention.choosingTime == nil }

    /// The message as a verb names it and the menus' words read it: as the server last said it stands, else its copy
    /// and its head.
    var shown: [API.FolderRow] {
        [standing ?? API.FolderRow(copy, head: reader.opened(copy), member: nil)]
    }

    /// Read how it stands: once the reader has opened it, and again after a mark.
    func reread() async {
        standing = await mailbox.standing(of: copy, head: await reader.head(of: copy))
    }

    func file(_ verb: API.FilingVerb) {
        let rows = shown
        Task { if await mailbox.fileNow(verb, rows: rows) { left = true } }
    }

    /// Ask where, over this window: the folders of the message's own account, but the one it is in.
    func askMove() {
        moving = MoveAsk(account: copy.account, from: copy.folder)
    }

    func move(to folder: API.RailFolder) {
        moving = nil
        let rows = shown
        Task { if await mailbox.moveNow(to: folder.name, label: Words.role(folder), rows: rows) { left = true } }
    }

    func setSeen(_ seen: Bool) {
        let rows = shown
        Task {
            await mailbox.setSeenNow(seen, rows: rows)
            await reread()
        }
    }

    func star(on: Bool) {
        let rows = shown
        Task {
            await mailbox.starNow(on: on, rows: rows)
            await reread()
        }
    }
}

/// A message window's head: an empty band the traffic lights stand on, and a press anywhere on it moves the window.
struct MessageWindowBar: View {
    var body: some View {
        Color.clear
            .frame(height: MessageWindow.barHeight)
            .frame(maxWidth: .infinity)
            .background { WindowDragArea() }
    }
}

private struct MessageWindowContent: View {
    let core: Core
    let copy: MailCopy
    let reader: ReaderModel
    let find: ReaderFind
    let verbs: MessageWindowVerbs?
    @Environment(\.meadow) private var meadow
    private var status: AppStatus { .shared }

    private var opened: API.MessageOpened? {
        if case .open(_, let opened) = reader.stage { return opened }
        return nil
    }

    var body: some View {
        VStack(spacing: 0) {
            // The bar the traffic lights stand on, which moves the window wherever it is pressed, as every window's
            // head does.
            MessageWindowBar()
            if find.open, opened != nil { ReaderFindBar(find: find) }
            content
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                .environment(\.readerFind, find)
        }
        .background { ZStack { meadow.paper; MeadowGrain() }.ignoresSafeArea() }
        .background { if let verbs { TypingWatch { verbs.typing = $0 } } }
        .background { MessageWindowKeys() }
        .overlay { if let verbs { MessageWindowDialog(verbs: verbs) } }
        .ignoresSafeArea()
        .meadowWindowChrome(barHeight: MessageWindow.barHeight)
        .navigationTitle(opened?.subject.flatMap { $0.isEmpty ? nil : $0 } ?? String(localized: "Message"))
        .focusedSceneValue(\.readerTools, opened.map { opened in
            ReaderTools(print: {
                MessagePrinter.shared.print(copy, opened: opened, core: core, prefs: ReadingPrefs(status.preferences),
                                            window: NSApp.keyWindow)
            }, find: { find.show() })
        })
        // The Message menu acts on this message: Reply, Reply All and Forward answer it once it has opened (a draft
        // is edited instead), and its other verbs act through `verbs`.
        .focusedSceneValue(\.mailWriting, opened.map { opened in
            MailWriting(replyTo: opened.isDraft == true ? nil : copy, draft: opened.isDraft == true ? copy : nil)
        })
        .focusedSceneValue(\.messageWindow, verbs)
    }

    @ViewBuilder private var content: some View {
        switch reader.stage {
        case .empty, .loading:
            Fetching()
        case .failed(_, let why):
            PaneNote(title: String(localized: "This message couldn’t be opened"), line: why)
        case .open(let copy, let opened):
            // How it stands says when it came as the list says it, and the mailbox names its account and folder.
            MessageReader(core: core, copy: copy, opened: opened, row: verbs?.standing,
                          prefs: ReadingPrefs(status.preferences), mine: Composing.shared.mine,
                          mailbox: verbs?.mailbox)
        }
    }
}

/// What a message window's Move To and a snooze's time of one's own ask, in Meadow's dialog over this window, as
/// the mail window asks them over its own: a folder of the message's account, or a day and an hour.
private struct MessageWindowDialog: View {
    let verbs: MessageWindowVerbs

    var body: some View {
        if let ask = verbs.moving {
            MeadowDialog(close: close) {
                MoveToSheet(mailbox: verbs.mailbox, ask: ask, moves: { verbs.move(to: $0) }, close: close)
            }
        } else if let copies = verbs.attention.choosingTime {
            MeadowDialog(place: .form, close: close) {
                SnoozeTimeDialog(copies: copies, model: verbs.attention, close: close)
            }
        }
    }

    private func close() {
        verbs.moving = nil
        verbs.attention.choosingTime = nil
    }
}

/// The keyboard in a message's window while nothing else there has it: as it opens, and once a field or a dialog
/// lets it go. Space, the arrows, Page Up and Down, Home and End scroll the message, as a Mac's reader does; any other
/// key goes on to the window and its menus.
struct MessageWindowKeys: NSViewRepresentable {
    func makeNSView(context: Context) -> Keys { Keys() }

    func updateNSView(_ view: Keys, context: Context) {}

    final class Keys: NSView {
        private var watching: NSKeyValueObservation?

        override var acceptsFirstResponder: Bool { true }

        override func viewDidMoveToWindow() {
            super.viewDidMoveToWindow()
            watching = nil
            guard let window else { return }
            window.initialFirstResponder = self
            watching = window.observe(\.firstResponder, options: [.initial, .new]) { [weak self] _, _ in
                DispatchQueue.main.async { MainActor.assumeIsolated { self?.takeIfNowhere() } }
            }
        }

        func takeIfNowhere() {
            guard let window, DialogFocus.nowhere(window.firstResponder, in: window) else { return }
            window.makeFirstResponder(self)
        }

        override func keyDown(with event: NSEvent) {
            if !ReaderPage.scrolls(event, in: window) { super.keyDown(with: event) }
        }
    }
}
#endif

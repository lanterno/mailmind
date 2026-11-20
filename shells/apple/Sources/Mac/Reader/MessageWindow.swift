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

    /// `content` inside the mail window's desk, as the pane's reader is, so the cards it puts in a reader show here
    /// too: none while no desk has joined the mailbox.
    static func inDesk(of mailbox: Mailbox, _ content: some View) -> AnyView {
        mailbox.inDesk?(AnyView(content)) ?? AnyView(content)
    }

    var body: some View {
        if let copy, let core = Self.core(of: app) {
            Self.inDesk(of: app.mailbox, MessageWindowContent(core: core, copy: copy, reader: reader, find: find,
                                                              verbs: verbs))
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
/// window's toast shows, and the mail window's. The message stands for itself, as the window shows it, never a whole
/// conversation.
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
    /// The message has left (filed, deleted, junked, moved, snoozed), from this window or any other: the window
    /// closes, and Undo opens it again (`Mailbox.sentAway`).
    private(set) var left = false

    init(copy: MailCopy, mailbox: Mailbox, reader: ReaderModel, routes: (any RouteCaller)?) {
        self.copy = copy
        self.mailbox = mailbox
        self.reader = reader
        let notices = DeskNotices()
        attention = AttentionModel(link: CoreLink(routes), notices: notices)
        // On Edit ▸ Undo too, as the mail window's are: z and the toast's Undo take it back once between them.
        notices.relay = { [weak mailbox] text, undo in
            mailbox?.say(text, undo: undo)
            if let undo { mailbox?.offerUndo(undo) }
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
        mailbox.opened(window: self)
    }

    /// A verb from any window sent its message away: the window closes.
    func sentAway() {
        left = true
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

    /// Snooze's chooser is up over this window, as h asks when.
    var choosingSnooze = false

    /// The page reader's single keys, on this message alone, as the mail window's list runs them on the message open
    /// there (`RowKeys`, and the other areas' `Mailbox.keys`): e, d (or # and ⌫), b, m, s, u and z; r, a and f once
    /// the message has opened, never on a draft; c; and attention's h, w and l. Whether `key` is one of them.
    func key(_ key: Character) -> Bool {
        if key == "z" {
            mailbox.undo()
            return true
        }
        guard acting, !choosingSnooze else { return false }
        switch key {
        case "e": file(.archive)
        case "d", "#", "\u{7F}": file(.trash)
        case "b": file(.junk)
        case "m": askMove()
        case "s": star(on: !Marks(shown).unstars)
        case "u": setSeen(false)
        case "c": Composing.shared.newMessage(current: copy.account)
        case "r", "a", "f":
            guard let opened = reader.opened(copy), opened.isDraft != true else { return true }
            let copy = copy
            Task {
                switch key {
                case "r": await Composing.shared.answer(copy)
                case "a": await Composing.shared.reply(copy, all: true)
                default: await Composing.shared.forward(copy)
                }
            }
        default:
            let attending = AttentionCommands.list(copies: [copy], stack: nil, model: attention) { [weak self] _ in
                self?.choosingSnooze = true
            }
            guard let command = attending.first(where: { $0.key == key }) else { return false }
            command.run()
        }
        return true
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

struct MessageWindowContent: View {
    let core: Core
    let copy: MailCopy
    let reader: ReaderModel
    let find: ReaderFind
    let verbs: MessageWindowVerbs?
    @Environment(\.meadow) private var meadow
    @Environment(Desk.self) private var desk: Desk?
    /// Where the window's reply box stands, so its toasts stand clear of it: a test's own, else the window's.
    var room: ToastRoom?
    @State private var ownRoom = ToastRoom()
    private var toastRoom: ToastRoom { room ?? ownRoom }
    @State private var snoozeMenu = MeadowMenuModel()
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
            // The document is laid out as the pane's is at the window's width: its margins, a phone's up to 700
            // points, and the aside beside the message from 1100.
            GeometryReader { room in
                content
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                    .environment(\.meadowPaneWidth, room.size.width)
            }
            .linkStatusLine()
            .environment(\.readerFind, find)
        }
        .background { ZStack { meadow.paper; MeadowGrain() }.ignoresSafeArea() }
        .background { if let verbs { TypingWatch { verbs.typing = $0 } } }
        .background { MessageWindowKeys(verbs: verbs, find: opened == nil ? nil : { find.show() }) }
        .environment(\.toastRoom, toastRoom)
        // What the window's verbs and keys say, with Undo, where the person is as well as in the mail window, clear
        // of the reply box.
        .overlay {
            if let verbs {
                PlacedToasts(room: toastRoom) { WindowToasts(mailbox: verbs.mailbox, status: status, desk: desk) }
            }
        }
        .overlay(alignment: .topLeading) { if let verbs { snoozeChooser(verbs) } }
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

    /// h's chooser, hung near the middle of the window, as the mail window hangs one with no Snooze button to hang
    /// it from.
    @ViewBuilder private func snoozeChooser(_ verbs: MessageWindowVerbs) -> some View {
        if verbs.choosingSnooze {
            GeometryReader { window in
                Color.clear
                    .frame(width: 0, height: 0)
                    .meadowPopover(isPresented: Binding(get: { true }, set: { if !$0 { verbs.choosingSnooze = false } }),
                                   keys: { snoozeMenu.key($0) }) {
                        SnoozeChooser(copies: [verbs.copy], model: verbs.attention, menu: snoozeMenu) {
                            verbs.choosingSnooze = false
                        }
                    }
                    .padding(.leading, window.size.width / 2 - 130)
                    .padding(.top, window.size.height / 3)
                    .onAppear { snoozeMenu.at = 0 }
            }
            .allowsHitTesting(false)
            .accessibilityHidden(true)
        }
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
/// key goes on to the window and its menus, and a single key nothing took, from here or from the message's frame, is
/// the reader's (`Catcher`). ⌘F finds in the message once it has opened (`finds`). The window opens clear of the
/// message windows already open (`OwnWindowPlace`).
struct MessageWindowKeys: NSViewRepresentable {
    let verbs: MessageWindowVerbs?
    /// Opens Find in Message's bar, once the message has opened.
    var find: (() -> Void)?

    func makeNSView(context: Context) -> Keys { Keys() }

    func updateNSView(_ view: Keys, context: Context) {
        view.verbs = verbs
        view.find = find
    }

    /// Whether `event` is ⌘F, whatever the layout types for F (`SingleKey`).
    static func finds(_ event: NSEvent) -> Bool {
        event.type == .keyDown
            && event.modifierFlags.intersection([.command, .control, .option, .shift]) == .command
            && SingleKey.typed(event).map { $0 == "f" || $0 == "F" } == true
    }

    final class Keys: NSView {
        private var watching: NSKeyValueObservation?
        weak var verbs: MessageWindowVerbs? {
            didSet { if let window { Catcher.hang(on: window).verbs = verbs } }
        }
        var find: (() -> Void)?

        override var acceptsFirstResponder: Bool { true }

        override func viewDidMoveToWindow() {
            super.viewDidMoveToWindow()
            watching = nil
            guard let window else { return }
            window.initialFirstResponder = self
            Catcher.hang(on: window).verbs = verbs
            OwnWindowPlace.opened(window)
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

        /// ⌘F finds in the message, as Edit › Find in Message says there. A window hears a key equivalent before the
        /// menu bar does, so ⌘F finds here even while the menus read another window or none: SwiftUI hands them a
        /// window's focused values only once its scene has said them, and Mailbox › Search holds ⌘F otherwise.
        override func performKeyEquivalent(with event: NSEvent) -> Bool {
            guard let find, event.window === window, MessageWindowKeys.finds(event) else {
                return super.performKeyEquivalent(with: event)
            }
            find()
            return true
        }
    }

    /// The responder after a message's window, which the window holds: the single keys nothing in the window took,
    /// run on its message (`MessageWindowVerbs.key`), never while text is typed there.
    final class Catcher: NSResponder {
        weak var verbs: MessageWindowVerbs?
        weak var window: NSWindow?
        private nonisolated(unsafe) static var held: UInt8 = 0

        /// Stand after `window` in its chain, once.
        @discardableResult
        static func hang(on window: NSWindow) -> Catcher {
            var next = window.nextResponder
            while let responder = next {
                if let held = responder as? Catcher { return held }
                next = responder.nextResponder
            }
            let catcher = Catcher()
            catcher.window = window
            catcher.nextResponder = window.nextResponder
            window.nextResponder = catcher
            objc_setAssociatedObject(window, &held, catcher, .OBJC_ASSOCIATION_RETAIN_NONATOMIC)
            return catcher
        }

        override func keyDown(with event: NSEvent) {
            guard let verbs, let window, let key = Self.single(event, in: window, verbs: verbs), verbs.key(key) else {
                return super.keyDown(with: event)
            }
        }

        /// The single key `event` is, when one of the reader's could be and nothing is typed: a character with no ⌘,
        /// ⌃ or ⌥ (⇧ only for # and ?), or Delete, never Space, Return, Esc, Tab or the arrows; and only while the
        /// single keys are on.
        static func single(_ event: NSEvent, in window: NSWindow, verbs: MessageWindowVerbs) -> Character? {
            guard verbs.mailbox.singleKeys, !verbs.typing,
                  !UntakenKeys.Catcher.typesText(window.firstResponder),
                  event.modifierFlags.intersection([.command, .control, .option]).isEmpty,
                  let key = SingleKey.typed(event), let scalar = key.unicodeScalars.first,
                  (scalar.value > 0x20 && scalar.value < 0x7F) || scalar.value == 0x7F
            else { return nil }
            guard !event.modifierFlags.contains(.shift) || "#?".contains(key) else { return nil }
            return Character(key.lowercased())
        }
    }
}

/// Where a message's own window opens: a step down and to the right of any message window already open where it
/// would stand, as a Mac cascades its windows, so the second never hides the first.
@MainActor
enum OwnWindowPlace {
    /// How far each window stands from the one it would cover.
    static let step: CGFloat = 22
    private static var windows: [Weak] = []

    private struct Weak {
        weak var window: NSWindow?
    }

    /// `window` has opened: it moves clear of the others, now and once SwiftUI has placed it.
    static func opened(_ window: NSWindow) {
        windows.removeAll { $0.window == nil || $0.window === window }
        place(window)
        DispatchQueue.main.async { MainActor.assumeIsolated { place(window) } }
        windows.append(Weak(window: window))
    }

    private static func place(_ window: NSWindow) {
        let others = windows.compactMap(\.window).filter { $0 !== window && $0.isVisible }.map(\.frame)
        let at = topLeft(of: window.frame, among: others)
        if at != NSPoint(x: window.frame.minX, y: window.frame.maxY) { window.setFrameTopLeftPoint(at) }
    }

    /// Where a window with `frame` stands its top left: where it is, else a step down and right of each window
    /// already standing there.
    static func topLeft(of frame: NSRect, among others: [NSRect]) -> NSPoint {
        var at = NSPoint(x: frame.minX, y: frame.maxY)
        func covers(_ other: NSRect) -> Bool { abs(other.minX - at.x) < 4 && abs(other.maxY - at.y) < 4 }
        while others.contains(where: covers) {
            at = NSPoint(x: at.x + step, y: at.y - step)
        }
        return at
    }
}
#endif

#if os(macOS)
import AppKit
import Observation
import SwiftUI

/// The window in front, as AppKit says which window is key, and what it shows: a message in a window of its own, or a
/// composer. The menu bar acts on it. SwiftUI hands the menus a window's focused values only once its scene has said
/// them, and a message window's were stale or missing while it was key, so Message › Reply there did nothing; AppKit
/// always knows which window is key. Each window says what it shows with `keyWindowShows`.
@MainActor
@Observable
final class KeyWindow {
    /// The process's: AppKit's windows, as the app's notification centre tells of them, from the window key as it is
    /// first asked for.
    static let shared: KeyWindow = {
        let made = KeyWindow(center: .default)
        if let key = NSApp?.keyWindow { made.read(key) }
        return made
    }()

    /// What a window shows.
    enum Shows {
        case message(MessageWindowVerbs)
        case composer(Composer)
    }

    /// Whether AppKit has said which window is key: until it has, the menus go by what SwiftUI hands them.
    private(set) var heard = false
    /// The message whose own window is key.
    private(set) var message: MessageWindowVerbs?
    /// The composer whose window is key.
    private(set) var composer: Composer?
    /// The window File › Close closes: the key one, or the window a popover's panel hangs from; nil with none open.
    @ObservationIgnored private(set) weak var closes: NSWindow?
    /// Whether there is a window to close, which File › Close follows.
    private(set) var canClose = false
    @ObservationIgnored private weak var key: NSWindow?
    @ObservationIgnored private var watching: [any NSObjectProtocol] = []

    init(center: NotificationCenter) {
        watching = [
            center.addObserver(forName: NSWindow.didBecomeKeyNotification, object: nil, queue: .main) { [weak self] note in
                let window = note.object as? NSWindow
                MainActor.assumeIsolated { self?.read(window) }
            },
            // A window that stops being key, or closes, leaves none key until another says it is.
            center.addObserver(forName: NSWindow.didResignKeyNotification, object: nil, queue: .main) { [weak self] note in
                let window = note.object as? NSWindow
                MainActor.assumeIsolated { self?.left(window) }
            },
            center.addObserver(forName: NSWindow.willCloseNotification, object: nil, queue: .main) { [weak self] note in
                let window = note.object as? NSWindow
                MainActor.assumeIsolated { self?.left(window) }
            },
        ]
    }

    private func left(_ window: NSWindow?) {
        if let window, key === window { read(nil) }
    }

    /// `window` is key now (nil: none is): what the menus act on.
    func read(_ window: NSWindow?) {
        key = window
        var shown: Shows?
        var at = window
        // A popover's panel, or a sheet, stands for the window it hangs from.
        while let one = at, shown == nil {
            if let tag = tags[ObjectIdentifier(one)], tag.window === one { shown = tag.shows }
            at = one.parent ?? one.sheetParent
        }
        var message: MessageWindowVerbs?, composer: Composer?
        switch shown {
        case .message(let verbs)?: message = verbs
        case .composer(let made)?: composer = made
        case nil: break
        }
        // A popover's panel closes the window it hangs from; a sheet, which waits for an answer, nothing.
        var closing = window
        while let one = closing, !one.styleMask.contains(.closable), !one.isSheet { closing = one.parent }
        if closing?.styleMask.contains(.closable) != true { closing = nil }
        closes = closing
        // Only what changed is said again: a window says what it shows at each of its updates.
        if !heard { heard = true }
        if self.message !== message { self.message = message }
        if self.composer !== composer { self.composer = composer }
        if canClose != (closing != nil) { canClose = closing != nil }
    }

    /// File › Close: the window in front closes, as its close button closes it.
    func close() {
        closes?.performClose(nil)
    }

    /// The message window the Message menu acts on: the key one, as AppKit says; before AppKit has said, what SwiftUI
    /// handed the menus. Never one that has closed.
    func messageWindow(focused: MessageWindowVerbs?) -> MessageWindowVerbs? {
        MailWindowDoor.live(heard ? message : focused)
    }

    /// What the window in front can write: a key message window's message (a draft is edited instead), else what
    /// SwiftUI handed the menus.
    func writing(focused: MailWriting?) -> MailWriting? {
        guard heard, let verbs = MailWindowDoor.live(message) else { return focused }
        return verbs.writing
    }

    // MARK: What each window shows

    private struct Tag {
        weak var window: NSWindow?
        let shows: Shows
    }

    /// What each window shows, by the window, while it is open.
    @ObservationIgnored private var tags: [ObjectIdentifier: Tag] = [:]

    /// `window` shows `shows` (nil: nothing the menus act on). Said again while it is key, the menus follow at once.
    func tag(_ window: NSWindow, _ shows: Shows?) {
        tags = tags.filter { $0.value.window != nil }
        tags[ObjectIdentifier(window)] = shows.map { Tag(window: window, shows: $0) }
        if key === window || (!heard && window.isKeyWindow) { read(window) }
    }
}

/// File › Close (⌘W), in place of SwiftUI's, which stayed on with no window to close: the window in front closes, as
/// its close button closes it, and with none open the item waits.
struct CloseCommands: Commands {
    var body: some Commands {
        CommandGroup(replacing: .saveItem) { CloseItem(keyWindow: .shared) }
    }
}

/// The item itself, a view of its own so a test draws it as AppKit's menu.
struct CloseItem: View {
    let keyWindow: KeyWindow

    var body: some View {
        Button("Close") { keyWindow.close() }
            .keyboardShortcut("w")
            .disabled(!keyWindow.canClose)
    }
}

extension MessageWindowVerbs {
    /// What the Message menu's Reply, Reply All and Forward answer here, once the message has opened; a draft is
    /// edited instead.
    var writing: MailWriting? {
        guard !left, let opened = reader.opened(copy) else { return nil }
        let draft = opened.isDraft == true
        return MailWriting(replyTo: draft ? nil : copy, draft: draft ? copy : nil)
    }
}

extension View {
    /// Tell the menu bar what this window shows, for while it is key (`KeyWindow`).
    func keyWindowShows(_ shows: KeyWindow.Shows?, in tracker: KeyWindow = .shared) -> some View {
        background { KeyWindowTag(shows: shows, tracker: tracker) }
    }
}

/// An empty view that tags the window it stands in.
private struct KeyWindowTag: NSViewRepresentable {
    let shows: KeyWindow.Shows?
    let tracker: KeyWindow

    func makeNSView(context: Context) -> Tagging { Tagging() }

    func updateNSView(_ view: Tagging, context: Context) {
        view.tracker = tracker
        view.shows = shows
    }

    final class Tagging: NSView {
        var tracker: KeyWindow?
        var shows: KeyWindow.Shows? {
            didSet { tell() }
        }

        override func viewDidMoveToWindow() {
            super.viewDidMoveToWindow()
            tell()
        }

        private func tell() {
            guard let window, let tracker else { return }
            tracker.tag(window, shows)
        }
    }
}
#endif

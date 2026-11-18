#if os(macOS)
import AppKit
import SwiftUI

/// Tells a window's model whether text is being typed in it, as the window's first responder changes: while a text
/// that can be edited has the keys (the search, the reader's reply box, Find's field, a dialog's), ⌘⌫, Delete, # and
/// the single keys are the text's, and never act on mail. The mail window's mailbox watches its window this way, and a
/// message's own window its verbs.
struct TypingWatch: NSViewRepresentable {
    let typing: @MainActor (Bool) -> Void

    func makeNSView(context: Context) -> Probe { Probe(typing) }

    func updateNSView(_ view: Probe, context: Context) {
        view.typing = typing
    }

    /// Whether `responder` takes typing: a field's editor, or a text view that can be edited.
    static func types(_ responder: NSResponder?) -> Bool {
        (responder as? NSTextView)?.isEditable == true
    }

    final class Probe: NSView {
        var typing: @MainActor (Bool) -> Void
        private var watching: NSKeyValueObservation?
        /// What it said last: the first responder changes far more often than whether text has the keys.
        private var said: Bool?

        init(_ typing: @escaping @MainActor (Bool) -> Void) {
            self.typing = typing
            super.init(frame: .zero)
        }

        required init?(coder: NSCoder) { nil }

        override func viewDidMoveToWindow() {
            super.viewDidMoveToWindow()
            watching = nil
            // Gone from its window (the window closed): nothing is typed there any more.
            guard let window else { return say(false) }
            watching = window.observe(\.firstResponder, options: [.initial, .new]) { [weak self] window, _ in
                MainActor.assumeIsolated {
                    self?.say(TypingWatch.types(window.firstResponder))
                }
            }
        }

        private func say(_ now: Bool) {
            guard now != said else { return }
            said = now
            typing(now)
        }
    }
}
#endif

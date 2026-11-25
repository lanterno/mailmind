#if os(macOS)
import AppKit
import SwiftUI
import UniformTypeIdentifiers

/// The message's text: plain text, Markdown as the server reads it. A paste whose links carry tracking goes in
/// without it, through the text's own edit so Undo still takes it back; a picture pasted or dropped by itself goes
/// into the text where the cursor is; other files dropped are attachments; Tab brings in a snippet and then goes
/// through its placeholders.
struct ComposeTextView: NSViewRepresentable {
    let composer: Composer
    /// What the composer holds, read where the window's body is drawn so a change reaches the text view.
    let text: String
    let selectionRequest: NSRange?
    let focusBody: Bool
    /// The light's ink and face for the text (Meadow's `.cp-body`).
    var look: ComposeTextLook?
    /// What ⌘Return does while the text has the keys, where something besides the menu's should answer it; it is
    /// told whether the press only repeats one (the key held down).
    var commandReturn: ((_ held: Bool) -> Void)?
    /// What Esc does while the text has the keys, where something should answer it: whether it took the key.
    var escape: (() -> Bool)?

    func makeCoordinator() -> Coordinator {
        Coordinator(composer: composer)
    }

    func makeNSView(context: Context) -> NSScrollView {
        let scroll = NSScrollView()
        scroll.hasVerticalScroller = true
        scroll.autohidesScrollers = true
        let text = BodyTextView(frame: .zero)
        text.isVerticallyResizable = true
        text.isHorizontallyResizable = false
        text.autoresizingMask = [.width]
        text.textContainer?.widthTracksTextView = true
        text.minSize = .zero
        text.maxSize = NSSize(width: CGFloat.greatestFiniteMagnitude, height: CGFloat.greatestFiniteMagnitude)
        scroll.documentView = text
        text.composer = composer
        text.delegate = context.coordinator
        text.isRichText = false
        text.importsGraphics = false
        text.allowsUndo = true
        text.usesFindBar = true
        text.isIncrementalSearchingEnabled = true
        text.font = .systemFont(ofSize: NSFont.systemFontSize + 1)
        text.textContainerInset = NSSize(width: 12, height: 10)
        text.commandReturn = commandReturn
        text.escape = escape
        // The caret where the composer has it, not where setting the text leaves it, at the end: a new message's is
        // at the top, above the signature, so what is typed goes above it, as Mail has it.
        let caret = composer.selection
        text.string = composer.body
        text.setSelectedRange(Self.inside(caret, of: composer.body))
        context.coordinator.synced = composer.body
        if let look {
            // The mockup's padding is the text's own distance from the edge, with no fragment padding inside it.
            text.textContainer?.lineFragmentPadding = 0
            text.textContainerInset = ComposeTextLook.inset
            text.drawsBackground = false
            look.dress(text)
            context.coordinator.look = look
        }
        text.setAccessibilityLabel("message body")
        text.registerForDraggedTypes([.fileURL])
        scroll.drawsBackground = false
        context.coordinator.text = text
        return scroll
    }

    func updateNSView(_ scroll: NSScrollView, context: Context) {
        guard let view = scroll.documentView as? BodyTextView else { return }
        view.commandReturn = commandReturn
        view.escape = escape
        // The composer's text as it is now, not as this view's value was made: a value made before the last key
        // reached the composer would put the older text back over what was just typed. `text` only says when to look.
        let held = composer.body, coordinator = context.coordinator
        if held != view.string && view.string == coordinator.synced {
            // A change the composer made (a snippet, a draft, a picture's mark): the text takes it whole, in the look,
            // and the caret stays where it was unless the composer asks for another place.
            let caret = view.selectedRange()
            view.string = held
            look?.dress(view)
            if selectionRequest == nil { view.setSelectedRange(Self.inside(caret, of: held)) }
            coordinator.synced = held
        } else if held != view.string {
            // Words in the text the composer never heard of: they are the person's, and the composer takes them,
            // never the other way round.
            let typed = view.string, caret = view.selectedRange()
            coordinator.synced = typed
            DispatchQueue.main.async { [composer = self.composer] in   // after this update, never during it
                composer.setBody(typed, selection: caret)
            }
            if let look, coordinator.look != look { look.dress(view) }
        } else if let look, coordinator.look != look {
            look.dress(view)
        }
        coordinator.look = look
        if let wanted = selectionRequest {
            let range = Self.inside(wanted, of: view.string)
            view.setSelectedRange(range)
            view.scrollRangeToVisible(range)
        }
        let focus = focusBody
        if focus {
            if let window = view.window { window.makeFirstResponder(view) } else { view.focusOnWindow = true }
        }
        guard selectionRequest != nil || focus else { return }
        // Taken: the requests are cleared after this update, never during it.
        let composer = composer
        DispatchQueue.main.async {
            if selectionRequest != nil { composer.selectionRequest = nil }
            if focus, composer.focusRequest == .body { composer.focusRequest = nil }
        }
    }

    /// A range inside `text`: the text may have changed since the range was taken.
    static func inside(_ range: NSRange, of text: String) -> NSRange {
        let length = (text as NSString).length, start = min(range.location, length)
        return NSRange(location: start, length: max(0, min(NSMaxRange(range), length) - start))
    }

    @MainActor
    final class Coordinator: NSObject, NSTextViewDelegate {
        let composer: Composer
        weak var text: BodyTextView?
        /// The look the text was last dressed in.
        var look: ComposeTextLook?
        /// The text the view and the composer last agreed on: which of them changed since says which way it goes.
        var synced = ""

        init(composer: Composer) {
            self.composer = composer
        }

        func textDidChange(_ notification: Notification) {
            guard let text else { return }
            synced = text.string
            composer.setBody(text.string, selection: text.selectedRange())
        }

        func textViewDidChangeSelection(_ notification: Notification) {
            guard let text else { return }
            composer.caretMoved(text.selectedRange())
        }

        func textView(_ textView: NSTextView, doCommandBy selector: Selector) -> Bool {
            composer.caretMoved(textView.selectedRange())
            switch selector {
            case #selector(NSResponder.insertTab(_:)): return composer.tabInBody(back: false)
            case #selector(NSResponder.insertBacktab(_:)): return composer.tabInBody(back: true)
            default: return false
            }
        }
    }
}

/// The text view under the composer's body, for what pasting and dropping do.
final class BodyTextView: NSTextView {
    weak var composer: Composer?
    /// Asked for the keys before it was in a window: it takes them as it gets one.
    var focusOnWindow = false
    /// ⌘Return while this text has the keys, ahead of the menu's: the reader's reply box sends with it, where the
    /// main window's View menu would otherwise take it for focus reading. Every auto-repeat of a held key comes here
    /// too, so it says which are.
    var commandReturn: ((_ held: Bool) -> Void)?
    /// Esc while this text has the keys, ahead of the text's own completion: the reader's reply box closes with it.
    var escape: (() -> Bool)?

    override func cancelOperation(_ sender: Any?) {
        if let escape, escape() { return }
        super.cancelOperation(sender)
    }

    override func performKeyEquivalent(with event: NSEvent) -> Bool {
        if let commandReturn, window?.firstResponder === self,
           event.modifierFlags.intersection(.deviceIndependentFlagsMask) == .command,
           event.charactersIgnoringModifiers == "\r" {
            commandReturn(event.repeatsAPress)
            return true
        }
        return super.performKeyEquivalent(with: event)
    }

    override func viewDidMoveToWindow() {
        super.viewDidMoveToWindow()
        guard focusOnWindow, let window else { return }
        focusOnWindow = false
        window.makeFirstResponder(self)
    }

    override func paste(_ sender: Any?) {
        let board = AppSeams.pasteboard
        if let pasted = board.string(forType: .string) {
            let clean = LinkClean.clean(pasted)
            guard clean != pasted else { return super.paste(sender) }
            insertText(clean, replacementRange: selectedRange())
            return
        }
        let pictures = Self.pictures(on: board)
        guard !pictures.isEmpty, let composer else { return super.paste(sender) }
        composer.caretMoved(selectedRange())
        composer.add(pictures, inText: true)
    }

    /// The pictures a pasteboard holds by themselves: files of a picture's type, or a picture's own bytes.
    static func pictures(on board: NSPasteboard) -> [Composer.Source] {
        if let urls = board.readObjects(forClasses: [NSURL.self], options: [.urlReadingFileURLsOnly: true]) as? [URL],
           !urls.isEmpty
        {
            return urls.filter { Pictures.isPicture(UTType(filenameExtension: $0.pathExtension)?.preferredMIMEType ?? "") }
                .map { .file($0) }
        }
        if let png = board.data(forType: .png) { return [.data(name: "image.png", type: "image/png", png)] }
        if let tiff = board.data(forType: .tiff), let image = NSBitmapImageRep(data: tiff),
           let png = image.representation(using: .png, properties: [:])
        {
            return [.data(name: "image.png", type: "image/png", png)]
        }
        return []
    }

    override func performDragOperation(_ sender: any NSDraggingInfo) -> Bool {
        let board = sender.draggingPasteboard
        guard let composer,
              let urls = board.readObjects(forClasses: [NSURL.self], options: [.urlReadingFileURLsOnly: true]) as? [URL],
              !urls.isEmpty else { return super.performDragOperation(sender) }
        // A picture goes where it was dropped; any other file is attached.
        let at = characterIndexForInsertion(at: convert(sender.draggingLocation, from: nil))
        composer.caretMoved(NSRange(location: at, length: 0))
        let pictures = urls.filter { Pictures.isPicture(UTType(filenameExtension: $0.pathExtension)?.preferredMIMEType ?? "") }
        let others = urls.filter { !pictures.contains($0) }
        if !pictures.isEmpty { composer.add(pictures.map { .file($0) }, inText: true) }
        if !others.isEmpty { composer.add(others.map { .file($0) }) }
        return true
    }
}

extension NSEvent {
    /// Whether this event only repeats a press: a held key's auto-repeat, or a double click's later clicks. A send
    /// takes a fresh press, so such an event never sends. `isARepeat` and `clickCount` raise for events that have
    /// neither, so each is asked only of its own kind.
    var repeatsAPress: Bool {
        switch type {
        case .keyDown, .keyUp: isARepeat
        case .leftMouseDown, .leftMouseUp: clickCount > 1
        default: false
        }
    }

    /// Whether the event the app is handling only repeats a press: what a button's action asks, which is given no
    /// event of its own.
    @MainActor static var repeatingAPress: Bool {
        NSApp?.currentEvent?.repeatsAPress ?? false
    }
}

/// What is being typed into a recipient field: a native field, so the completion list takes the arrow keys and
/// Return before the field does, and Backspace in an empty field takes the last chip back.
struct RecipientInput: NSViewRepresentable {
    let label: String
    let placeholder: String
    @Binding var text: String
    /// The completion's keys: up or down (-1, 1), which says if it was taken; and Return or Tab, which takes the
    /// suggestion highlighted, else what is typed, and says which (`RecipientCompletion`).
    let move: (Int) -> Bool
    let take: () -> RecipientCompletion.Taken
    /// A comma or semicolon typed: what is typed becomes a chip.
    let commit: () -> Void
    let backspaceWhenEmpty: () -> Void
    let escape: () -> Bool
    let focused: Bool
    let focusTaken: () -> Void
    let lostFocus: () -> Void
    /// What is typed, and the placeholder: the light's ink and its quietest (Meadow's 14.5-point field).
    var ink: NSColor?
    var quiet: NSColor?

    func makeCoordinator() -> Coordinator {
        Coordinator(self)
    }

    /// The field's face and its placeholder in the light's inks.
    private func dress(_ field: RecipientField) {
        guard let ink else { return }
        field.font = .meadow(.ui, 14.5)
        field.textColor = ink
        field.placeholderAttributedString = NSAttributedString(string: placeholder, attributes: [
            .font: NSFont.meadow(.ui, 14.5), .foregroundColor: quiet ?? ink,
        ])
    }

    func makeNSView(context: Context) -> RecipientField {
        let field = RecipientField()
        field.isBordered = false
        field.drawsBackground = false
        field.focusRingType = .none
        field.placeholderString = placeholder
        // The field's name, on the field and on its cell, which AppKit lets speak for a text field.
        field.setAccessibilityLabel(label)
        field.cell?.setAccessibilityLabel(label)
        field.delegate = context.coordinator
        field.cell?.isScrollable = true
        field.cell?.wraps = false
        field.stringValue = text
        dress(field)
        return field
    }

    func updateNSView(_ field: RecipientField, context: Context) {
        context.coordinator.input = self
        if ink != nil, field.textColor != ink || field.placeholderAttributedString?.string != placeholder {
            dress(field)
        }
        if field.stringValue != text { field.stringValue = text }
        guard focused else { return }
        if let window = field.window {
            if field.currentEditor() == nil { window.makeFirstResponder(field) }
        } else {
            field.focusOnWindow = true
        }
        let taken = focusTaken
        DispatchQueue.main.async { taken() }   // after this update, never during it
    }

    @MainActor
    final class Coordinator: NSObject, NSTextFieldDelegate {
        var input: RecipientInput

        init(_ input: RecipientInput) {
            self.input = input
        }

        func controlTextDidChange(_ notification: Notification) {
            guard let field = notification.object as? NSTextField else { return }
            let value = field.stringValue
            // A comma or semicolon typed ends the chip.
            if let last = value.last, last == "," || last == ";" {
                input.text = value
                input.commit()
                return
            }
            input.text = value
        }

        func controlTextDidBeginEditing(_ notification: Notification) {
            input.focusTaken()
        }

        func controlTextDidEndEditing(_ notification: Notification) {
            input.lostFocus()
        }

        func control(_ control: NSControl, textView: NSTextView, doCommandBy selector: Selector) -> Bool {
            switch selector {
            case #selector(NSResponder.moveDown(_:)): return input.move(1)
            case #selector(NSResponder.moveUp(_:)): return input.move(-1)
            case #selector(NSResponder.insertNewline(_:)):
                return input.take() != .nothing
            case #selector(NSResponder.insertTab(_:)):
                // A suggestion taken keeps the keys here, for the next; what was typed, made a chip, moves on.
                if case .suggestion = input.take() { return true }
                return false
            case #selector(NSResponder.cancelOperation(_:)):
                return input.escape()
            case #selector(NSResponder.deleteBackward(_:)):
                if input.text.isEmpty {
                    input.backspaceWhenEmpty()
                    return true
                }
                return false
            default:
                return false
            }
        }
    }
}

/// A recipient field's text field, which takes the keys as it reaches a window when they were asked for before.
final class RecipientField: NSTextField {
    var focusOnWindow = false

    override func viewDidMoveToWindow() {
        super.viewDidMoveToWindow()
        guard focusOnWindow, let window else { return }
        focusOnWindow = false
        window.makeFirstResponder(self)
    }
}
#endif

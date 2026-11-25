import AppKit
import Carbon.HIToolbox
import XCTest

/// MailMindTestKit's own tests: keys under each layout it names, the mouse, where the keyboard is and the menu bar's
/// items as AppKit validates them, on plain AppKit views in windows off the screen.
@MainActor
final class TestKitTests: XCTestCase {
    private var windows: [NSWindow] = []
    private var restoreMenu: (() -> Void)?

    override func setUp() async throws {
        _ = NSApplication.shared
    }

    override func tearDown() async throws {
        for window in windows { window.orderOut(nil) }
        windows = []
        restoreMenu?()
        restoreMenu = nil
    }

    /// A window off the screen and see-through, key, its content a plain view.
    private func window(width: CGFloat = 400, height: CGFloat = 300, key: Bool = true) -> NSWindow {
        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: width, height: height), styleMask: [.titled],
                              backing: .buffered, defer: false)
        window.isReleasedWhenClosed = false
        window.contentView = NSView(frame: NSRect(x: 0, y: 0, width: width, height: height))
        windows.append(window)
        if key { RealKeys.key(window) } else { window.alphaValue = 0 }
        return window
    }

    private func installed(_ layout: KeyboardLayout) throws {
        do { _ = try layout.source() } catch { throw XCTSkip("\(error)") }
    }

    // MARK: Layouts

    func testUSTypesItsOwnLetters() throws {
        try installed(.us)
        XCTAssertEqual(try KeyboardLayout.us.characters(UInt16(kVK_ANSI_Z)).characters, "z")
        XCTAssertEqual(try KeyboardLayout.us.characters(UInt16(kVK_ANSI_Y)).characters, "y")
        XCTAssertEqual(try KeyboardLayout.us.characters(UInt16(kVK_ANSI_Z), held: .shift).characters, "Z")
        let return_ = try RealKeys.event(UInt16(kVK_Return), layout: .us)
        XCTAssertEqual(return_.characters, "\r", "Return is AppKit's \\r")
        let down = try RealKeys.event(UInt16(kVK_DownArrow), layout: .us)
        XCTAssertEqual(down.characters, String(Character(UnicodeScalar(NSDownArrowFunctionKey)!)))
        XCTAssertTrue(down.modifierFlags.contains(.function), "an arrow carries fn")
    }

    /// Arabic types its own letters, and with ⌘ held the Latin ones its table keeps for shortcuts.
    func testArabicTypesArabicAndLatinUnderCommand() throws {
        try installed(.arabic)
        XCTAssertEqual(try KeyboardLayout.arabic.characters(UInt16(kVK_ANSI_D)).characters, "ي")
        XCTAssertEqual(try KeyboardLayout.arabic.characters(UInt16(kVK_ANSI_E)).characters, "ث")
        let commandD = try RealKeys.event(UInt16(kVK_ANSI_D), held: .command, layout: .arabic)
        XCTAssertEqual(commandD.charactersIgnoringModifiers, "d")
    }

    /// A menu bar with Undo on ⌘Z, Redo on ⇧⌘Z and Yank on ⌘Y, which says which fired. Redo's key is written as
    /// AppKit's own Redo writes it, a capital Z with ⌘: AppKit matches a lowercase key with ⇧ in its mask by reading
    /// the key again under the Mac's own layout, which only a window server's event carries.
    private final class Fired: NSObject {
        var titles: [String] = []
        @objc func fire(_ sender: NSMenuItem) { titles.append(sender.title) }
    }

    private func editBar(_ fired: Fired) -> NSMenu {
        let bar = NSMenu()
        let edit = NSMenuItem(title: "Edit", action: nil, keyEquivalent: "")
        edit.submenu = NSMenu(title: "Edit")
        for (title, key, held) in [("Undo", "z", [.command]), ("Redo", "Z", [.command]),
                                   ("Yank", "y", [.command])] as [(String, String, NSEvent.ModifierFlags)]
        {
            let item = NSMenuItem(title: title, action: #selector(Fired.fire(_:)), keyEquivalent: key)
            item.keyEquivalentModifierMask = held
            item.target = fired
            edit.submenu?.addItem(item)
        }
        bar.addItem(edit)
        return bar
    }

    /// The key US's Z stands on is Y under German and Swiss French, and the other way round: ⌘ on it fires the item
    /// whose letter the layout types there, through the menu bar as a key equivalent.
    func testCommandShortcutsFollowTheLettersUnderGermanAndSwissFrench() throws {
        for layout in [KeyboardLayout.german, .swissFrench] {
            try installed(layout)
            let fired = Fired()
            let bar = editBar(fired)
            XCTAssertEqual(try layout.characters(UInt16(kVK_ANSI_Z)).characters, "y", "\(layout)")
            XCTAssertEqual(try layout.characters(UInt16(kVK_ANSI_Y)).characters, "z", "\(layout)")
            XCTAssertTrue(bar.performKeyEquivalent(with: try RealKeys.event(UInt16(kVK_ANSI_Z), held: .command,
                                                                            layout: layout)))
            XCTAssertTrue(bar.performKeyEquivalent(with: try RealKeys.event(UInt16(kVK_ANSI_Y), held: .command,
                                                                            layout: layout)))
            XCTAssertTrue(bar.performKeyEquivalent(with: try RealKeys.event(UInt16(kVK_ANSI_Y),
                                                                            held: [.command, .shift], layout: layout)))
            XCTAssertEqual(fired.titles, ["Yank", "Undo", "Redo"], "\(layout): the letters moved, and ⌘ with them")
        }
        try installed(.us)
        let fired = Fired()
        let bar = editBar(fired)
        XCTAssertTrue(bar.performKeyEquivalent(with: try RealKeys.event(UInt16(kVK_ANSI_Z), held: .command,
                                                                        layout: .us)))
        XCTAssertEqual(fired.titles, ["Undo"], "under US, the same key is ⌘Z")
    }

    /// The same, pressed in a key window with the menu bar as the app's: the key reaches the menu bar.
    func testCommandUnderGermanPressedInAWindowReachesTheMenuBar() throws {
        try installed(.german)
        let fired = Fired()
        let old = NSApp.mainMenu
        NSApp.mainMenu = editBar(fired)
        restoreMenu = { NSApp.mainMenu = old }
        let window = window()
        try RealKeys.press(UInt16(kVK_ANSI_Z), held: .command, layout: .german, in: window)
        XCTAssertEqual(fired.titles, ["Yank"])
    }

    func testALayoutThisMacLacksSaysSo() {
        XCTAssertThrowsError(try KeyboardLayout.source("com.apple.keylayout.NoSuchLayout")) { error in
            XCTAssertEqual("\(error)", "this Mac has no com.apple.keylayout.NoSuchLayout keyboard layout")
        }
    }

    /// The kit reads a layout's table and never changes the Mac's: the layout keys are typed on is the one it was.
    func testReadingLayoutsLeavesTheMacsOwn() throws {
        func current() -> String? {
            guard let source = TISCopyCurrentKeyboardLayoutInputSource()?.takeRetainedValue(),
                  let id = TISGetInputSourceProperty(source, kTISPropertyInputSourceID) else { return nil }
            return Unmanaged<CFString>.fromOpaque(id).takeUnretainedValue() as String
        }
        let before = current()
        for layout in KeyboardLayout.allCases { _ = try? layout.characters(UInt16(kVK_ANSI_A)) }
        XCTAssertEqual(current(), before)
    }

    // MARK: The mouse

    /// A button that tracks the mouse in its own `mouseDown`, as AppKit's did before gesture recognizers: AppKit's
    /// own button now takes a click through the window's recognizers, which only a key window's own routing reaches,
    /// so a plain `NSButton` is held by the hosted tests.
    private final class Tracking: NSButton {
        override func mouseDown(with event: NSEvent) {
            super.mouseDown(with: event)
        }
    }

    private final class Count: NSObject {
        var clicks = 0
        @objc func clicked(_ sender: Any?) { clicks += 1 }
    }

    /// A button pressed: it tracks the mouse itself until the button comes up, taking the rest of the click from the
    /// app's queue.
    func testAClickPressesAButton() throws {
        let window = window()
        let button = Tracking(title: "Send", target: nil, action: nil)
        button.frame = NSRect(x: 40, y: 40, width: 120, height: 32)
        window.contentView?.addSubview(button)
        let count = Count()
        button.target = count
        button.action = #selector(Count.clicked(_:))
        try RealMouse.click(button)
        XCTAssertEqual(count.clicks, 1, "one click, one press")
        XCTAssertFalse(NSApp.isActive, "a click never makes the app active")
        try RealMouse.click(at: CGPoint(x: 300, y: 250), in: try XCTUnwrap(window.contentView))
        XCTAssertEqual(count.clicks, 1, "a click beside it presses nothing")
    }

    /// A view that hears what reaches it, as the window routes it.
    private final class Pad: NSView {
        var heard: [String] = []
        var dragged: [CGPoint] = []
        override func mouseDown(with event: NSEvent) { heard.append("down \(event.clickCount)") }
        override func mouseUp(with event: NSEvent) { heard.append("up \(event.clickCount)") }
        override func mouseDragged(with event: NSEvent) {
            dragged.append(convert(event.locationInWindow, from: nil))
        }
    }

    func testADoubleClickReachesTheViewUnderThePointer() throws {
        let window = window()
        let pad = Pad(frame: NSRect(x: 100, y: 100, width: 100, height: 100))
        window.contentView?.addSubview(pad)
        try RealMouse.click(pad, count: 2)
        XCTAssertEqual(pad.heard, ["down 1", "up 1", "down 2", "up 2"])
    }

    func testADragMovesFromOnePointToTheOther() throws {
        let window = window()
        let pad = Pad(frame: NSRect(x: 0, y: 0, width: 300, height: 200))
        window.contentView?.addSubview(pad)
        try RealMouse.drag(from: CGPoint(x: 20, y: 20), to: CGPoint(x: 220, y: 120), in: pad, steps: 4)
        XCTAssertEqual(pad.heard, ["down 1", "up 1"])
        XCTAssertEqual(pad.dragged.count, 4)
        let last = try XCTUnwrap(pad.dragged.last)
        XCTAssertEqual(last.x, 220, accuracy: 0.5)
        XCTAssertEqual(last.y, 120, accuracy: 0.5)
    }

    func testTheWheelScrollsTheScrollViewUnderThePointer() throws {
        let window = window()
        let scroll = NSScrollView(frame: NSRect(x: 0, y: 0, width: 400, height: 300))
        let tall = NSView(frame: NSRect(x: 0, y: 0, width: 400, height: 3000))
        scroll.documentView = tall
        scroll.hasVerticalScroller = true
        window.contentView?.addSubview(scroll)
        tall.scroll(CGPoint(x: 0, y: 1500))
        try RealMouse.scroll(by: CGVector(dx: 0, dy: 120), at: CGPoint(x: 200, y: 150), in: scroll)
        XCTAssertEqual(scroll.contentView.bounds.minY, 1620, accuracy: 1, "120 points further up the document")
        try RealMouse.scroll(by: CGVector(dx: 0, dy: -240), at: CGPoint(x: 200, y: 150), in: scroll)
        XCTAssertEqual(scroll.contentView.bounds.minY, 1380, accuracy: 1, "and back down")
    }

    // MARK: Finding things

    func testFindsAViewByItsIdentifierAndByRoleAndLabelWithinARegion() throws {
        let window = window()
        let region = NSView(frame: NSRect(x: 0, y: 0, width: 200, height: 300))
        region.setAccessibilityIdentifier("composer")
        region.setAccessibilityElement(true)
        region.setAccessibilityRole(.group)
        let send = NSButton(title: "Send", target: nil, action: nil)
        send.frame = NSRect(x: 20, y: 20, width: 100, height: 30)
        region.addSubview(send)
        let other = NSButton(title: "Send", target: nil, action: nil)
        other.frame = NSRect(x: 220, y: 20, width: 100, height: 30)
        window.contentView?.addSubview(region)
        window.contentView?.addSubview(other)
        XCTAssertTrue(try Find.identifier("composer", in: window).view === region)
        XCTAssertThrowsError(try Find.role(.button, "Send", in: window), "two are Send in the window")
        let found = try Find.role(.button, "Send", within: "composer", in: window)
        XCTAssertTrue(found.view === send, "\(found)")
        let middle = try found.middle(in: send)
        XCTAssertEqual(middle.x, 50, accuracy: 1)
        XCTAssertEqual(middle.y, 15, accuracy: 1)
        XCTAssertThrowsError(try Find.role(.button, "Archive", in: window))
    }

    // MARK: Where the keyboard is

    func testAnEditableFieldHasTheKeyboard() throws {
        let window = window()
        let field = NSTextField(frame: NSRect(x: 20, y: 20, width: 200, height: 24))
        window.contentView?.addSubview(field)
        XCTAssertTrue(window.makeFirstResponder(field))
        XCTAssertNil(keyboardIsSomewhere(window), "the field's editor, in the field")
    }

    func testAnEditableTextViewHasTheKeyboard() throws {
        let window = window()
        let text = NSTextView(frame: NSRect(x: 20, y: 20, width: 200, height: 100))
        window.contentView?.addSubview(text)
        XCTAssertTrue(window.makeFirstResponder(text))
        XCTAssertNil(keyboardIsSomewhere(window))
        text.isEditable = false
        text.isSelectable = false
        XCTAssertNotNil(keyboardIsSomewhere(window), "a text view that takes no keys")
    }

    /// A view that takes the keyboard: a list's table, a button a person can press with Space.
    private final class Takes: NSView {
        override var acceptsFirstResponder: Bool { true }
    }

    func testAViewThatAcceptsTheKeyboardHasIt() throws {
        let window = window()
        let view = Takes(frame: NSRect(x: 20, y: 20, width: 100, height: 100))
        window.contentView?.addSubview(view)
        XCTAssertTrue(window.makeFirstResponder(view))
        XCTAssertNil(keyboardIsSomewhere(window))
    }

    func testTheWindowItselfIsNowhere() throws {
        let window = window()
        XCTAssertTrue(window.makeFirstResponder(nil))
        XCTAssertEqual(keyboardIsSomewhere(window), "the first responder is the window itself")
    }

    func testAHiddenViewIsNowhere() throws {
        let window = window()
        let holder = NSView(frame: NSRect(x: 0, y: 0, width: 200, height: 200))
        let view = Takes(frame: NSRect(x: 20, y: 20, width: 100, height: 100))
        holder.addSubview(view)
        window.contentView?.addSubview(holder)
        // AppKit takes the keyboard from a view as it is hidden, but not from one hidden already: a panel hidden
        // with the keyboard handed to what was in it.
        holder.isHidden = true
        XCTAssertTrue(window.makeFirstResponder(view))
        XCTAssertEqual(keyboardIsSomewhere(window), "the first responder Takes is hidden")
    }

    func testAViewOfNoSizeIsNowhere() throws {
        let window = window()
        let view = Takes(frame: NSRect(x: 20, y: 20, width: 0, height: 0))
        window.contentView?.addSubview(view)
        XCTAssertTrue(window.makeFirstResponder(view))
        XCTAssertEqual(keyboardIsSomewhere(window), "the first responder Takes has no size")
    }

    /// A panel closed with its field still first responder: the keys would go to a window no one sees.
    func testAViewInAClosedPanelIsNowhere() throws {
        let panel = NSPanel(contentRect: NSRect(x: 0, y: 0, width: 300, height: 200), styleMask: [.titled],
                            backing: .buffered, defer: false)
        panel.isReleasedWhenClosed = false
        panel.contentView = NSView(frame: NSRect(x: 0, y: 0, width: 300, height: 200))
        windows.append(panel)
        let field = NSTextField(frame: NSRect(x: 20, y: 20, width: 200, height: 24))
        panel.contentView?.addSubview(field)
        RealKeys.key(panel)
        XCTAssertTrue(panel.makeFirstResponder(field))
        XCTAssertNil(keyboardIsSomewhere(panel))
        panel.close()
        XCTAssertEqual(keyboardIsSomewhere(panel), "the window isn't on screen")
    }

    /// A window that can never be key, as a borderless one: what is typed never reaches it. (A key window and one
    /// behind it differ only in an active app, which a test's never is: the hosted tests hold that.)
    func testAWindowThatCantBeKeyIsNowhere() throws {
        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 300, height: 200), styleMask: [.borderless],
                              backing: .buffered, defer: false)
        window.isReleasedWhenClosed = false
        window.contentView = NSView(frame: NSRect(x: 0, y: 0, width: 300, height: 200))
        windows.append(window)
        RealKeys.key(window)
        let view = Takes(frame: NSRect(x: 20, y: 20, width: 100, height: 100))
        window.contentView?.addSubview(view)
        XCTAssertTrue(window.makeFirstResponder(view))
        XCTAssertEqual(keyboardIsSomewhere(window), NSApp.isActive ? "the window isn't key" : "the window can't be key")
    }

    /// A click on a field gives it the keyboard, as a person's does.
    func testAClickOnAFieldPutsTheKeyboardThere() throws {
        let window = window()
        let field = NSTextField(frame: NSRect(x: 20, y: 20, width: 200, height: 24))
        field.placeholderString = "Subject"
        window.contentView?.addSubview(field)
        XCTAssertNotNil(keyboardIsSomewhere(window), "nowhere yet")
        try RealMouse.click(try Find.role(.textField, "Subject", in: window), in: window)
        XCTAssertNil(keyboardIsSomewhere(window))
        try RealKeys.press(UInt16(kVK_ANSI_D), layout: .us, in: window)
        XCTAssertEqual(field.currentEditor()?.string, "d", "and what is typed goes into it")
    }

    // MARK: The menu bar's items

    /// A responder that says which of its items may be chosen, and which is checked, as the app's views do.
    private final class Judge: NSView, NSMenuItemValidation {
        var canReplyAll = true
        var flagged = false
        var replied = 0
        override var acceptsFirstResponder: Bool { true }
        @objc func replyAll(_ sender: Any?) { replied += 1 }
        @objc func toggleFlag(_ sender: Any?) {}

        func validateMenuItem(_ item: NSMenuItem) -> Bool {
            switch item.action {
            case #selector(replyAll(_:)): return canReplyAll
            case #selector(toggleFlag(_:)):
                item.state = flagged ? .on : .off
                return true
            default: return false
            }
        }
    }

    /// Message › Reply All and Flag, sent through the responder chain (no target), and Forward, which nothing
    /// answers.
    private func messageBar() -> NSMenu {
        let bar = NSMenu()
        bar.addItem(NSMenuItem(title: "MailMind", action: nil, keyEquivalent: ""))
        let message = NSMenuItem(title: "Message", action: nil, keyEquivalent: "")
        message.submenu = NSMenu(title: "Message")
        message.submenu?.addItem(NSMenuItem(title: "Reply All", action: #selector(Judge.replyAll(_:)),
                                            keyEquivalent: "R"))
        message.submenu?.addItem(NSMenuItem(title: "Flag", action: #selector(Judge.toggleFlag(_:)), keyEquivalent: ""))
        message.submenu?.addItem(NSMenuItem(title: "Forward", action: NSSelectorFromString("forwardTheMessage:"),
                                            keyEquivalent: "F"))
        bar.addItem(message)
        return bar
    }

    func testAMenuItemIsReadAsTheResponderChainValidatesIt() throws {
        let window = window()
        let judge = Judge(frame: NSRect(x: 20, y: 20, width: 100, height: 100))
        window.contentView?.addSubview(judge)
        XCTAssertTrue(window.makeFirstResponder(judge))
        let old = NSApp.mainMenu
        NSApp.mainMenu = messageBar()
        restoreMenu = { NSApp.mainMenu = old }

        XCTAssertEqual(try menuItem(["Message", "Reply All"], for: window),
                       MenuItemState(title: "Reply All", enabled: true, checked: false))
        judge.canReplyAll = false
        XCTAssertFalse(try menuItem(["Message", "Reply All"], for: window).enabled, "the responder said no")
        XCTAssertFalse(try menuItem(["Message", "Forward"], for: window).enabled, "nothing in the chain answers it")
        XCTAssertFalse(try menuItem(["Message", "Flag"], for: window).checked)
        judge.flagged = true
        XCTAssertTrue(try menuItem(["Message", "Flag"], for: window).checked, "checked as the responder says")
        XCTAssertThrowsError(try menuItem(["Message", "Archive"], for: window))
        XCTAssertThrowsError(try menuItem(["Mailbox", "Inbox"], for: window))

        XCTAssertThrowsError(try chooseMenuItem(["Message", "Reply All"], for: window), "a disabled item can't be chosen")
        judge.canReplyAll = true
        try chooseMenuItem(["Message", "Reply All"], for: window)
        XCTAssertEqual(judge.replied, 1)

        XCTAssertTrue(window.makeFirstResponder(nil))
        XCTAssertFalse(try menuItem(["Message", "Reply All"], for: window).enabled, "with the judge out of the chain, nothing answers")
    }
}
